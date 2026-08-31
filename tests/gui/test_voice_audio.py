"""Qt buffer/device boundary tests with fake devices; host audio is never opened."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtMultimedia import QtAudio

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.voice import SpeechAudio, VoiceError
from pc_manager_agent.ui import voice_audio as audio


class Device:
    missing = False
    supported = True

    def isNull(self):
        return self.missing

    def isFormatSupported(self, format):
        return self.supported


class Source(QObject):
    stateChanged = Signal(object)
    error_value = QtAudio.Error.NoError

    def __init__(self, device, format, parent):
        super().__init__(parent)
        self.was_reset = False
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(b"\0\0" * 240))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)

    def setBufferSize(self, size):
        assert size == 8192

    def start(self, buffer=None):
        return self.buffer

    def error(self):
        return self.error_value

    def reset(self):
        self.was_reset = True


@pytest.fixture
def fake_devices(monkeypatch):
    device = Device()
    monkeypatch.setattr(
        audio,
        "QMediaDevices",
        SimpleNamespace(defaultAudioInput=lambda: device, defaultAudioOutput=lambda: device),
    )
    monkeypatch.setattr(audio, "QAudioSource", Source)
    monkeypatch.setattr(audio, "QAudioSink", Source)
    return device


def test_capture_no_device_probe_until_start(qapp, monkeypatch):
    def blocked():
        raise AssertionError("unexpected host probe")

    monkeypatch.setattr(audio, "QMediaDevices", SimpleNamespace(defaultAudioInput=blocked))
    capture = audio.QtAudioCapture(VoiceSettings())
    playback = audio.QtAudioPlayback()
    capture.discard()
    playback.stop()
    with pytest.raises(VoiceError, match="NOT_RECORDING"):
        capture.stop()


def test_capture_stops_before_returning_pcm(qapp, fake_devices):
    capture = audio.QtAudioCapture(VoiceSettings())
    capture.start()
    source = capture._source
    with pytest.raises(VoiceError, match="ALREADY_ACTIVE"):
        capture.start()
    result = capture.stop()
    assert result.duration_seconds == 0.01 and source.was_reset
    assert capture._source is None and capture._samples.size == 0


@pytest.mark.parametrize(
    "attribute,code", [("missing", "UNAVAILABLE"), ("supported", "FORMAT_UNSUPPORTED")]
)
def test_unsupported_device_fails_closed(qapp, fake_devices, attribute, code):
    setattr(fake_devices, attribute, attribute == "missing")
    capture = audio.QtAudioCapture(VoiceSettings())
    with pytest.raises(VoiceError, match=code):
        capture.start()
    assert capture._source is None


def test_device_error_and_limit_discard(qapp, fake_devices, monkeypatch):
    capture = audio.QtAudioCapture(VoiceSettings())
    codes = []
    capture.failed.connect(codes.append)
    capture.start()
    source = capture._source
    source.error_value = QtAudio.Error.IOError
    capture._state_changed(QtAudio.State.StoppedState)
    assert source.was_reset and codes[-1] == "MICROPHONE_PERMISSION_OR_DEVICE_ERROR"
    capture.start()
    capture._limit()
    assert capture._source is None and codes[-1] == "VOICE_INPUT_LIMIT_REACHED"
    capture.start()
    capture._samples._maximum = 2
    capture._read()
    assert capture._source is None and capture._samples.size == 0
    monkeypatch.setattr(Source, "error_value", QtAudio.Error.OpenError)
    with pytest.raises(VoiceError, match="DEVICE_ERROR"):
        capture.start()


def test_playback_flush_idle_and_failure(qapp, fake_devices):
    playback = audio.QtAudioPlayback()
    pcm = SpeechAudio(pcm=b"00" * 240, provider="fake")
    playback.play(pcm)
    sink = playback._sink
    playback.stop()
    assert sink.was_reset and playback._buffer is None
    playback.play(pcm)
    playback._state_changed(QtAudio.State.IdleState)
    assert playback._sink is None
    playback.play(pcm)
    playback._sink.error_value = QtAudio.Error.IOError
    playback._state_changed(QtAudio.State.StoppedState)
    playback._state_changed(QtAudio.State.StoppedState)
    assert playback._sink is None
    fake_devices.missing = True
    with pytest.raises(VoiceError, match="UNAVAILABLE"):
        playback.play(pcm)
