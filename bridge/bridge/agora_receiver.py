"""Thin wrapper around the official Agora Python Server SDK.

Agora callbacks run on SDK threads, so the callback only copies PCM bytes and
hands them to the asyncio loop. ASR work never blocks the RTC callback.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class AgoraReceiver:
    _service: Any = None
    _appid: Optional[str] = None

    def __init__(
        self,
        appid: str,
        channel: str,
        token: str,
        uid: int,
        on_pcm: Callable[[bytes, int], None],
        on_network_stats: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.appid = appid
        self.channel = channel
        self.token = token
        self.uid = uid
        self.on_pcm = on_pcm
        self.on_network_stats = on_network_stats
        self.connection: Any = None
        self._observers = []

    async def start(self) -> None:
        try:
            from agora.rtc.agora_service import AgoraService, AgoraServiceConfig
            from agora.rtc.agora_base import (
                AudioProfileType,
                AudioScenarioType,
                AudioSubscriptionOptions,
                ChannelProfileType,
                ClientRoleType,
                RTCConnConfig,
                RtcConnectionPublishConfig,
            )
            from agora.rtc.audio_frame_observer import IAudioFrameObserver
            from agora.rtc.local_user_observer import IRTCLocalUserObserver
            from agora.rtc.rtc_connection_observer import IRTCConnectionObserver
        except ImportError as exc:
            raise RuntimeError(
                "Agora Python Server SDK is missing; install bridge/requirements.txt"
            ) from exc

        loop = asyncio.get_running_loop()
        receiver = self

        class ConnectionObserver(IRTCConnectionObserver):
            def on_connected(self, agora_rtc_conn, conn_info, reason):
                logger.info("Agora connected: uid=%s reason=%s", conn_info.local_user_id, reason)

            def on_disconnected(self, agora_rtc_conn, conn_info, reason):
                logger.info("Agora disconnected: reason=%s", reason)

            def on_user_joined(self, agora_rtc_conn, user_id):
                logger.info("Agora remote user joined: %s", user_id)

            def on_user_left(self, agora_rtc_conn, user_id, reason):
                logger.info("Agora remote user left: %s reason=%s", user_id, reason)

        class AudioObserver(IAudioFrameObserver):
            def on_playback_audio_frame_before_mixing(
                self,
                agora_local_user,
                channel_id,
                remote_uid,
                audio_frame,
                vad_result_state,
                vad_result_bytearray,
            ):
                pcm = bytes(audio_frame.buffer)
                if pcm:
                    received_ns = time.perf_counter_ns()
                    loop.call_soon_threadsafe(receiver.on_pcm, pcm, received_ns)
                return 1

        class LocalUserObserver(IRTCLocalUserObserver):
            def on_remote_audio_track_statistics(
                self, agora_local_user, agora_remote_audio_track, stats
            ):
                if stats is None or receiver.on_network_stats is None:
                    return
                snapshot = {
                    "networkTransportDelayMs": stats.network_transport_delay,
                    "jitterBufferDelayMs": stats.jitter_buffer_delay,
                    "audioLossRatePercent": stats.audio_loss_rate,
                    "receivedSampleRate": stats.received_sample_rate,
                    "receivedBitrateKbps": stats.received_bitrate,
                    "frozenRatePercent": stats.frozen_rate,
                }
                loop.call_soon_threadsafe(receiver.on_network_stats, snapshot)

        if AgoraReceiver._service is None:
            service_config = AgoraServiceConfig()
            service_config.appid = self.appid
            service_config.enable_video = 0
            # DEFAULT avoids requiring the browser-specific AIClient scenario.
            service_config.audio_scenario = AudioScenarioType.AUDIO_SCENARIO_DEFAULT
            service = AgoraService()
            result = service.initialize(service_config)
            if isinstance(result, int) and result < 0:
                raise RuntimeError("AgoraService.initialize failed: " + str(result))
            AgoraReceiver._service = service
            AgoraReceiver._appid = self.appid
        elif AgoraReceiver._appid != self.appid:
            raise RuntimeError("One bridge process cannot switch Agora App IDs")

        subscription = AudioSubscriptionOptions(
            packet_only=0,
            pcm_data_only=1,
            bytes_per_sample=2,
            number_of_channels=1,
            sample_rate_hz=16_000,
        )
        connection_config = RTCConnConfig(
            auto_subscribe_audio=1,
            auto_subscribe_video=0,
            client_role_type=ClientRoleType.CLIENT_ROLE_BROADCASTER,
            channel_profile=ChannelProfileType.CHANNEL_PROFILE_LIVE_BROADCASTING,
            audio_recv_media_packet=0,
            audio_subs_options=subscription,
            enable_audio_recording_or_playout=0,
        )
        publish_config = RtcConnectionPublishConfig(
            audio_profile=AudioProfileType.AUDIO_PROFILE_DEFAULT,
            audio_scenario=AudioScenarioType.AUDIO_SCENARIO_DEFAULT,
            is_publish_audio=False,
            is_publish_video=False,
        )
        connection = AgoraReceiver._service.create_rtc_connection(
            connection_config, publish_config
        )
        if connection is None:
            raise RuntimeError("AgoraService.create_rtc_connection returned no connection")
        connection_observer = ConnectionObserver()
        audio_observer = AudioObserver()
        local_user_observer = LocalUserObserver()
        self._observers = [connection_observer, audio_observer, local_user_observer]
        self.connection = connection

        result = connection.register_observer(connection_observer)
        if isinstance(result, int) and result < 0:
            self.stop()
            raise RuntimeError("Agora connection observer registration failed: " + str(result))
        local_user = connection.get_local_user()
        if local_user is None:
            self.stop()
            raise RuntimeError("Agora connection did not provide a local user")
        result = connection.register_local_user_observer(local_user_observer)
        if isinstance(result, int) and result < 0:
            self.stop()
            raise RuntimeError("Agora local user observer registration failed: " + str(result))
        result = local_user.set_playback_audio_frame_before_mixing_parameters(1, 16_000)
        if isinstance(result, int) and result < 0:
            self.stop()
            raise RuntimeError("Agora PCM format setup failed: " + str(result))
        # The current SDK requires explicit VAD arguments. We use the transparent
        # PCM callback and keep the replaceable demo segmenter in our own process.
        result = connection.register_audio_frame_observer(audio_observer, 0, None)
        if isinstance(result, int) and result < 0:
            self.stop()
            raise RuntimeError("Agora audio observer registration failed: " + str(result))
        result = local_user.subscribe_all_audio()
        if isinstance(result, int) and result < 0:
            self.stop()
            raise RuntimeError("Agora subscribe_all_audio failed: " + str(result))
        result = connection.connect(self.token, self.channel, str(self.uid))
        if isinstance(result, int) and result < 0:
            self.stop()
            raise RuntimeError("Agora connection failed: " + str(result))

    def stop(self) -> None:
        if self.connection is None:
            return
        try:
            self.connection.disconnect()
        finally:
            self.connection.release()
            self.connection = None
            self._observers = []

    @classmethod
    def shutdown_service(cls) -> None:
        if cls._service is not None:
            cls._service.release()
            cls._service = None
            cls._appid = None
