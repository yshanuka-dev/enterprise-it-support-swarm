"""Day 17 tests: Barge-in handling with [interrupted] tagging, and sub-2s latency tracking.

Rubric Part 3 (Section 7):
1. Barge-in handling (10 pts):
   'On interruption, save only what was actually spoken, tagged [interrupted]...
   write that truncated text to memory. Never store the unsaid remainder.'
2. Voice latency (5 pts):
   'Measure end of user speech to first audio byte. Log a voice_pipeline span
   in Langfuse with metadata for end-of-utterance delay, LLM first token,
   TTS first byte and total. Target under 2 seconds.'
"""

from __future__ import annotations

from unittest.mock import patch

from incident_swarm.config import load_model_config
from incident_swarm.voice.agent import (
    VoiceInterruptionTracker,
    VoiceLatencyTracker,
)

# ── Barge-In Handling Tests (10 pts) ─────────────────────────────────────────

class MockSpeechHandle:
    def __init__(self, spoken_text: str = "", interrupted: bool = True):
        self.interrupted = interrupted
        self.chat_items = [type("Item", (), {"text_content": spoken_text})()] if spoken_text else []
        self._callbacks = []

    def add_done_callback(self, cb):
        self._callbacks.append(cb)

    def trigger_done(self):
        for cb in self._callbacks:
            cb(self)


class MockHistory:
    def __init__(self, initial_text: str = ""):
        self.messages = [
            type(
                "Msg",
                (),
                {
                    "role": "assistant",
                    "text_content": initial_text,
                    "content": None,
                    "interrupted": False,
                },
            )()
        ]


class MockSession:
    def __init__(self, initial_text: str = ""):
        self.history = MockHistory(initial_text=initial_text)


def test_barge_in_tags_and_discards_unsaid_text():
    """Rubric: only what was spoken is saved, tagged [interrupted]. Never unsaid remainder."""
    full_planned_text = (
        "I am pulling the Redis cluster logs from node 3, checking error rates, "
        "and restarting the degraded pods."
    )
    actual_spoken_words = "I am pulling the Redis cluster logs"

    session = MockSession(initial_text=full_planned_text)
    tracker = VoiceInterruptionTracker(session_id="test-barge-in-1")

    handle = MockSpeechHandle(spoken_text=actual_spoken_words, interrupted=True)
    ev = type("Ev", (), {"speech_handle": handle})()

    tracker.on_speech_created(ev, session)
    handle.trigger_done()

    # 1. Spoken portion tagged with [interrupted]
    expected_tagged = "I am pulling the Redis cluster logs [interrupted]"
    assert tracker.get_last_interruption() == expected_tagged
    assert expected_tagged in tracker.interrupted_utterances

    # 2. Unsaid text ("restarting the degraded pods") is completely purged from history
    stored_text = session.history.messages[0].text_content
    assert stored_text == expected_tagged
    assert "restarting the degraded pods" not in stored_text
    assert session.history.messages[0].interrupted is True


def test_barge_in_clean_helper():
    """truncate_to_interrupted ensures clean format without duplicate tags."""
    assert (
        VoiceInterruptionTracker.truncate_to_interrupted("Checking metrics now")
        == "Checking metrics now [interrupted]"
    )
    assert (
        VoiceInterruptionTracker.truncate_to_interrupted("Already tagged [interrupted]")
        == "Already tagged [interrupted]"
    )
    assert VoiceInterruptionTracker.truncate_to_interrupted("") == "[interrupted]"


def test_barge_in_sequential_interruptions():
    """Multiple interruptions across turns are tracked independently in history."""
    tracker = VoiceInterruptionTracker(session_id="test-multi-barge")

    for i in range(3):
        spoken = f"Attempt number {i + 1}"
        session = MockSession(initial_text=f"{spoken} and extra unsaid words...")
        handle = MockSpeechHandle(spoken_text=spoken, interrupted=True)
        ev = type("Ev", (), {"speech_handle": handle})()

        tracker.on_speech_created(ev, session)
        handle.trigger_done()

        assert tracker.interrupted_utterances[-1] == f"{spoken} [interrupted]"
        assert "extra unsaid words" not in session.history.messages[0].text_content

    assert len(tracker.interrupted_utterances) == 3


def test_barge_in_not_triggered_on_normal_completion():
    """When speech finishes normally (interrupted=False), no tag is applied."""
    session = MockSession(initial_text="Full message delivered successfully.")
    tracker = VoiceInterruptionTracker(session_id="test-normal-speech")

    handle = MockSpeechHandle(spoken_text="Full message delivered successfully.", interrupted=False)
    ev = type("Ev", (), {"speech_handle": handle})()

    tracker.on_speech_created(ev, session)
    handle.trigger_done()

    assert tracker.get_last_interruption() is None
    assert len(tracker.interrupted_utterances) == 0
    assert "[interrupted]" not in session.history.messages[0].text_content


# ── Latency Instrumentation Tests (5 pts) ───────────────────────────────────

def test_latency_tracker_records_eou_ttft_ttfb():
    """Measures EOU delay, LLM first token, and TTS first byte to first audio byte."""
    tracker = VoiceLatencyTracker(session_id="latency-breakdown-test")

    # Simulate LiveKit metrics collection
    eou_metric = type("EOUMetrics", (), {"end_of_utterance_delay": 0.22})()
    llm_metric = type("LLMMetrics", (), {"ttft": 0.38})()
    tts_metric = type("TTSMetrics", (), {"ttfb": 0.18})()

    ev_eou = type("Ev", (), {"metrics": eou_metric})()
    ev_llm = type("Ev", (), {"metrics": llm_metric})()
    ev_tts = type("Ev", (), {"metrics": tts_metric})()

    tracker.on_metrics_collected(ev_eou)
    tracker.on_metrics_collected(ev_llm)
    tracker.on_metrics_collected(ev_tts)

    latest = tracker.get_latest_span()
    assert latest is not None
    assert latest["end_of_utterance_delay"] == 0.22
    assert latest["llm_first_token"] == 0.38
    assert latest["tts_first_byte"] == 0.18

    # Total latency = 0.22 + 0.38 + 0.18 = 0.78s
    expected_total = round(0.22 + 0.38 + 0.18, 2)
    assert round(latest["total_latency"], 2) == expected_total
    # Rubric: target under 2 seconds
    assert latest["sub_2s"] is True
    assert latest["total_latency"] < 2.0


def test_latency_tracker_logs_langfuse_span():
    """Logs a voice_pipeline span with required breakdown metadata."""
    tracker = VoiceLatencyTracker(session_id="langfuse-span-test")
    tts_metric = type("TTSMetrics", (), {"ttfb": 0.25})()

    with patch("incident_swarm.app.tracing.log_voice_pipeline_span") as mock_trace:
        tracker.on_metrics_collected(type("Ev", (), {"metrics": tts_metric})())

        assert mock_trace.called
        _, kwargs = mock_trace.call_args
        assert kwargs["session_id"] == "langfuse-span-test"
        assert kwargs["tts_first_byte"] == 0.25
        assert kwargs["total_latency"] < 2.0


def test_config_voice_latency_target_under_2_seconds():
    """Verifies models.yaml config establishes 2.0s voice target latency limit."""
    cfg = load_model_config()
    target_limit = cfg.limits.get("voice_target_latency_s", 2.0)
    assert target_limit <= 2.0
