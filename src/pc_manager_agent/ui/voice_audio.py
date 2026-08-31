"""Qt audio hardware behind explicit PTT; construction never enumerates or opens devices."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QAudioSource, QMediaDevices, QtAudio

from pc_manager_agent.config.voice import VoiceSettings
from pc_manager_agent.domain.voice import CapturedAudio, SpeechAudio, VoiceError
from pc_manager_agent.voice.audio import BoundedAudioBuffer


def internal_format() -> QAudioFormat:
    """Describe the sole tested V1 format; unsupported devices fail explicitly, never guess."""
    result = QAudioFormat()
    result.setSampleRate(24000)
    result.setChannelCount(1)
    result.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return result


class QtAudioCapture(QObject):
    """One bounded input device, opened exclusively by an explicit visible PTT gesture."""

    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, settings: VoiceSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._samples = BoundedAudioBuffer(settings)
        self._source: QAudioSource | None = None
        self._device: QIODevice | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._limit)

    def start(self) -> None:
        """Open only the current default input; absence/permission/format errors are terminal."""
        if self._source is not None:
            raise VoiceError("VOICE_INPUT_ALREADY_ACTIVE")
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise VoiceError("MICROPHONE_UNAVAILABLE")
        audio_format = internal_format()
        if not device.isFormatSupported(audio_format):
            raise VoiceError("MICROPHONE_FORMAT_UNSUPPORTED")
        self._samples.clear()
        source = QAudioSource(device, audio_format, self)
        source.setBufferSize(8192)
        self._source = source
        self._device = source.start()
        if self._device is None or source.error() != QtAudio.Error.NoError:
            self.discard()
            raise VoiceError("MICROPHONE_PERMISSION_OR_DEVICE_ERROR")
        self._device.readyRead.connect(self._read)
        source.stateChanged.connect(self._state_changed)
        self._timer.start(self._settings.max_voice_input_seconds * 1000)

    def stop(self) -> CapturedAudio:
        """Stop the device first and transfer the bounded complete samples, never a disk file."""
        if self._source is None:
            raise VoiceError("VOICE_NOT_RECORDING")
        self._read()
        self._stop_device()
        return self._samples.finish()

    def discard(self) -> None:
        """Stop hardware regardless of journal availability, then release pending audio."""
        self._stop_device()
        self._samples.clear()

    def _stop_device(self) -> None:
        self._timer.stop()
        source, self._source = self._source, None
        self._device = None
        if source is not None:
            source.reset()
            source.deleteLater()

    def _read(self) -> None:
        try:
            # Bound each event-loop turn as well as total memory. Qt retains the remainder.
            for _ in range(8):
                if self._device is None or not self._device.bytesAvailable():
                    break
                self._samples.append(bytes(self._device.read(8192).data()))
            self.progress.emit(self._samples.size // 48)
        except VoiceError as exc:
            self.discard()
            self.failed.emit(exc.code)

    def _limit(self) -> None:
        self.discard()
        self.failed.emit("VOICE_INPUT_LIMIT_REACHED")

    def _state_changed(self, state: QtAudio.State) -> None:
        if self._source and self._source.error() != QtAudio.Error.NoError:
            self.discard()
            self.failed.emit("MICROPHONE_PERMISSION_OR_DEVICE_ERROR")


class QtAudioPlayback(QObject):
    """One PCM-only output; reset flushes queued speech before a new recording can start."""

    finished = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None

    def play(self, audio: SpeechAudio) -> None:
        """Play already-consented bounded synthetic speech only, with no file/URL decoder."""
        self.stop()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull() or not device.isFormatSupported(internal_format()):
            raise VoiceError("VOICE_OUTPUT_DEVICE_OR_FORMAT_UNAVAILABLE")
        buffer = QBuffer(self)
        buffer.setData(QByteArray(audio.pcm))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._buffer = buffer
        self._sink = QAudioSink(device, internal_format(), self)
        self._sink.stateChanged.connect(self._state_changed)
        self._sink.start(buffer)

    def stop(self) -> None:
        """Immediately flush native queued audio and release the in-memory PCM copy."""
        sink, self._sink = self._sink, None
        if sink is not None:
            sink.reset()
            sink.deleteLater()
        buffer, self._buffer = self._buffer, None
        if buffer is not None:
            buffer.close()
            buffer.setData(QByteArray())
            buffer.deleteLater()

    def _state_changed(self, state: QtAudio.State) -> None:
        if self._sink is None:
            return
        if self._sink.error() != QtAudio.Error.NoError:
            self.stop()
            self.failed.emit("VOICE_OUTPUT_FAILED")
        elif state == QtAudio.State.IdleState:
            self.stop()
            self.finished.emit()
