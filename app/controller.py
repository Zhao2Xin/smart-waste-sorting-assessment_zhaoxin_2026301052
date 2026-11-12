"""与 UI 无关的分拣状态机骨架。"""

import time
from enum import Enum
from typing import Callable, Dict, Optional

from .detectors import Detection
from .protocol import Command, Packet, WasteType, WASTE_NAMES
from .transport import Transport, TransportError


class SystemState(Enum):
    IDLE = "等待识别"
    WAIT_ACK = "等待下位机确认"
    EXECUTING = "下位机执行中"
    COOLDOWN = "等待物体离开"
    COMPRESSING = "压缩中"
    PAUSED = "已暂停"
    ERROR = "通信异常"


EventCallback = Callable[[str, dict], None]


class SortingController:
    def __init__(
        self,
        transport: Transport,
        event_callback: Optional[EventCallback] = None,
        stable_frames: int = 3,
        ack_timeout: float = 2.0,
        max_retries: int = 2,
        compress_after: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.event_callback = event_callback or (lambda _name, _data: None)
        self.stable_frames = stable_frames
        self.ack_timeout = ack_timeout
        self.max_retries = max_retries
        self.compress_after = compress_after
        self.clock = clock
        self.counts = {item: 0 for item in WasteType}
        self.bin_full = {item: False for item in WasteType}
        self.state = SystemState.IDLE
        self.pending = None  # type: Optional[WasteType]
        self._candidate = None  # type: Optional[WasteType]
        self._stable_count = 0
        self._missing_frames = 0
        self._last_packet = None  # type: Optional[Packet]
        self._deadline = 0.0
        self._retry_count = 0
        self._compress_sent = False
        self._sequence = 0
        self._active_sequence = None  # type: Optional[int]
        # 用于判断压缩完成后回到 COOLDOWN还是IDLE
        self._after_compressing = SystemState.COOLDOWN

    def _emit(self, name: str, **data) -> None:
        self.event_callback(name, data)

    def _set_state(self, state: SystemState) -> None:
        if state != self.state:
            self.state = state
            self._emit("state", value=state.value)

    def reset(self) -> None:
        self.counts = {item: 0 for item in WasteType}
        self.bin_full = {item: False for item in WasteType}
        self.pending = None
        self._candidate = None
        self._stable_count = 0
        self._missing_frames = 0
        self._last_packet = None
        self._retry_count = 0
        self._compress_sent = False
        self._active_sequence = None
        self._set_state(SystemState.IDLE)
        self._emit("counts", values=self.named_counts())
        self._emit("bins", values=self.named_bins())

    def named_counts(self) -> Dict[str, int]:
        return {WASTE_NAMES[key]: value for key, value in self.counts.items()}

    def named_bins(self) -> Dict[str, bool]:
        return {WASTE_NAMES[key]: value for key, value in self.bin_full.items()}

    def process_detection(self, detection: Optional[Detection]) -> None:
        """TODO 4：完成稳定帧判定、冷却消抖和分类指令触发。"""
        if self.state == SystemState.IDLE:
            if detection is None:
                self._stable_count = 0
                self._candidate = None
                return
            waste_type = detection.waste_type
            if self._stable_count == 0:
                self._candidate = waste_type
                self._stable_count = 1
                if self.stable_frames == 1:
                    self._candidate = None
                    self._stable_count = 0
                    self._send_classification(waste_type)
                return
            if waste_type == self._candidate:
                self._stable_count += 1
                if self._stable_count >= self.stable_frames:
                    self._candidate = None
                    self._stable_count = 0
                    self._send_classification(waste_type)
                return
            else:
                self._stable_count = 1
                self._candidate = waste_type
                return
        if self.state == SystemState.COOLDOWN:
            if detection is None:
                self._missing_frames += 1
                if self._missing_frames >= 8:
                    self._missing_frames = 0
                    self._set_state(SystemState.IDLE)
                return
            else:
                self._missing_frames = 0
        if self.state == SystemState.COMPRESSING or (
            self.state == SystemState.WAIT_ACK
            and self._last_packet is not None
            and self._last_packet.command == Command.START_COMPRESS
        ):
            if detection is None:
                self._missing_frames += 1
                if self._missing_frames >= 8:
                    self._missing_frames = 0
                    self._after_compressing = SystemState.IDLE
                return
            else:
                self._missing_frames = 0
                

    def _send_packet(self, packet: Packet, wait_for_reply: bool = True) -> None:
        try:
            self.transport.send(packet)
        except TransportError as exc:
            self._set_state(SystemState.ERROR)
            self._emit("log", message="发送失败：{}".format(exc))
            return
        self._emit("tx", packet=packet.encode().hex(" ").upper())
        if wait_for_reply:
            self._last_packet = packet
            self._deadline = self.clock() + self.ack_timeout
            self._retry_count = 0

    def _send_classification(self, waste_type: WasteType) -> None:
        """TODO 5：处理满载、序号，并发送 WASTE_RESULT。"""
        if self.bin_full[waste_type]:
            self._set_state(SystemState.COOLDOWN)
            return
        else:
            self._sequence = (self._sequence + 1) & 0xFF # 保证序号小于256
            self._active_sequence = self._sequence
            self.pending = waste_type
            packet = Packet(Command.WASTE_RESULT, bytes([self._sequence, waste_type]))
            self._set_state(SystemState.WAIT_ACK)
            self._send_packet(packet)
            

    def poll(self) -> None:
        """TODO 6：轮询接收；超时后重发，超过次数进入 ERROR。"""
        for packet in self.transport.poll():
            self._emit("rx", packet=packet.encode().hex(" ").upper())
            self.handle_packet(packet)
        if self.state not in (SystemState.WAIT_ACK, SystemState.EXECUTING, SystemState.COMPRESSING):
            return
        if self._deadline > self.clock():
            return
        if self._retry_count >= self.max_retries:
            self._set_state(SystemState.ERROR)
            return
        # 超时重发
        self._send_packet(self._last_packet, wait_for_reply=False)
        if self.state == SystemState.ERROR:
            return
        self._retry_count += 1
        self._deadline = self.clock() + self.ack_timeout

    def handle_packet(self, packet: Packet) -> None:
        """TODO 7：处理 ACK、ACTION_DONE、BIN_FULL 和 ERROR。"""
        command = packet.command
        payload = packet.payload
        if command == Command.ACK:
            if self.state != SystemState.WAIT_ACK:
                return
            if payload == bytes([Command.START_COMPRESS, self._active_sequence]) and self._last_packet.command == Command.START_COMPRESS:
                self._set_state(SystemState.COMPRESSING)
            elif payload == bytes([Command.WASTE_RESULT, self._active_sequence]) and self._last_packet.command == Command.WASTE_RESULT:
                self._set_state(SystemState.EXECUTING)
            else:
                return
            self._deadline = self.clock() + self.ack_timeout
            
        elif command == Command.ACTION_DONE:
            if self.state == SystemState.EXECUTING:
                if payload == bytes([self._active_sequence]):
                    self.counts[self.pending] += 1
                    self.pending = None
                    self._active_sequence = None
                    self._last_packet = None      
                    self._set_state(SystemState.COOLDOWN)    
                    self._emit("counts", values=self.named_counts())
                    # 达到阈值后压缩
                    if sum(self.counts.values()) == self.compress_after:                         
                        self._maybe_compress()
            elif self.state == SystemState.COMPRESSING and (bytes([self._active_sequence]) == payload):
                self._active_sequence = None
                self._last_packet = None
                if self._after_compressing == SystemState.IDLE:
                    self._missing_frames = 0
                self._set_state(self._after_compressing)
        elif command == Command.BIN_FULL:
            if len(payload) != 1 or not 0x01 <= payload[0] <= 0x04:
                return
            self.bin_full[payload[0]] = True
            self._emit("bins", values=self.named_bins())
        elif command == Command.ERROR:
            message = payload.decode("utf-8", errors="replace")
            self._set_state(SystemState.ERROR)
            self._emit("log", message=f"下位机报错：{message}")




    def _maybe_compress(self) -> None:
        """加分项：达到阈值后发送一次 START_COMPRESS。"""

        self._after_compressing = SystemState.COOLDOWN
        self._sequence = (self._sequence + 1) & 0xFF
        self._active_sequence = self._sequence
        self._set_state(SystemState.WAIT_ACK)
        self._send_packet(Packet(Command.START_COMPRESS, bytes([self._sequence])))
        self._compress_sent = True
