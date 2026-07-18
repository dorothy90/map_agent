from pydantic import ValidationError
import pytest

from job_models import JobCreate, JobStatus, assert_transition, is_terminal


def test_waiting_input_can_resume_to_queue():
    assert_transition(JobStatus.WAITING_INPUT, JobStatus.QUEUED)


def test_terminal_state_cannot_transition():
    with pytest.raises(ValueError, match="terminal"):
        assert_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)


def test_create_rejects_blank_query():
    with pytest.raises(ValidationError):
        JobCreate(query="   ", session_id="session-1")


def test_terminal_statuses():
    assert is_terminal(JobStatus.CANCELLED)
    assert not is_terminal(JobStatus.WAITING_INPUT)
