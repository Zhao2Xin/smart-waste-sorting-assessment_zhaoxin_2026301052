"""Translate and group the TX/RX messages already emitted by the worker."""

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from .protocol import Command, Packet, WASTE_NAMES


ACTION_NAMES = {Command.WASTE_RESULT: "分拣", Command.START_COMPRESS: "压缩"}
ROUND_COLORS = ("#e8f4ef", "#eaf1fa")


def decode_log_frame(raw: str) -> Optional[Packet]:
    try:
        frame = bytes.fromhex(raw)
        if len(frame) >= 5:
            packet = Packet(frame[3], frame[4:-1])
            if packet.encode() == frame:
                return packet
    except ValueError:
        pass
    return None


@dataclass
class Round:
    command: int
    sequence: int
    title: str
    waste: Optional[int]
    item: QtWidgets.QTreeWidgetItem
    color: str
    confirmed: bool = False


class CommunicationView(QtWidgets.QTreeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setIndentation(13)
        self.setMinimumHeight(135)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self._show_raw = False
        self._active = None
        self._last_sort = None
        self._round_count = 0

    def clear_history(self) -> None:
        self._active = self._last_sort = None
        self._round_count = 0
        self.clear()

    def show_raw(self, enabled: bool) -> None:
        self._show_raw = enabled
        iterator = QtWidgets.QTreeWidgetItemIterator(self)
        while iterator.value() is not None:
            item = iterator.value()
            data = item.data(0, QtCore.Qt.UserRole)
            if data:
                text, raw = data
                item.setText(0, text + ("\n    " + raw if enabled else ""))
            iterator += 1

    def interrupt_round(self, status: str) -> None:
        if self._active is not None:
            self._set_status(self._active, status)
        self._active = self._last_sort = None

    def _add_top(self, item: QtWidgets.QTreeWidgetItem) -> None:
        at_top = self.verticalScrollBar().value() == 0
        self.insertTopLevelItem(0, item)
        if self.topLevelItemCount() > 180:
            old = self.takeTopLevelItem(self.topLevelItemCount() - 1)
            if self._active is not None and self._active.item is old:
                self._active = None
            if self._last_sort is not None and self._last_sort[0].item is old:
                self._last_sort = None
        if at_top:
            self.scrollToTop()

    @staticmethod
    def _set_status(round_: Round, status: str) -> None:
        round_.item.setText(0, "{}  ·  {}".format(round_.title, status))

    def _append(self, timestamp: str, message: str, raw: str = "", round_: Optional[Round] = None) -> None:
        text = "{}  {}".format(timestamp, message)
        item = QtWidgets.QTreeWidgetItem([text])
        item.setToolTip(0, text + ("\n" + raw if raw else ""))
        if raw:
            item.setData(0, QtCore.Qt.UserRole, (text, raw))
            if self._show_raw:
                item.setText(0, text + "\n    " + raw)
        if round_ is None:
            self._add_top(item)
        else:
            item.setBackground(0, QtGui.QBrush(QtGui.QColor(round_.color)))
            round_.item.addChild(item)
            if round_.item.childCount() > 24:
                round_.item.takeChild(0)

    def append_message(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        direction, _, raw = message.partition("  ")
        if direction not in ("TX", "RX"):
            if message.startswith("发送失败") and self._active is not None:
                self._set_status(self._active, "发送失败")
                self._active = None
            self._append(timestamp, message)
            return
        packet = decode_log_frame(raw)
        round_, description = self._describe(direction, packet)
        self._append(timestamp, "{}  {}".format(direction, description), raw, round_)

    def _describe(self, direction: str, packet: Optional[Packet]):
        if packet is None:
            return None, "无法解析报文"
        command, payload = packet.command, packet.payload
        active = self._active
        if direction == "TX" and command in ACTION_NAMES:
            expected_size = 2 if command == Command.WASTE_RESULT else 1
            if len(payload) == expected_size:
                sequence = payload[0]
                waste = payload[1] if expected_size == 2 else None
                name = ACTION_NAMES[command]
                if active is not None and (active.command, active.sequence, active.waste) == (command, sequence, waste):
                    return active, "重发{}指令".format(name)
                if active is not None:
                    self._set_status(active, "未完成")
                category = " · " + WASTE_NAMES.get(waste, "未知类别") if waste is not None else ""
                title = "{}{}  #{}".format(name, category, sequence)
                color = ROUND_COLORS[self._round_count % len(ROUND_COLORS)]
                self._round_count += 1
                item = QtWidgets.QTreeWidgetItem()
                item.setBackground(0, QtGui.QBrush(QtGui.QColor(color)))
                item.setFont(0, QtGui.QFont(self.font().family(), self.font().pointSize(), QtGui.QFont.DemiBold))
                active = self._active = Round(command, sequence, title, waste, item, color)
                self._set_status(active, "等待确认")
                self._add_top(item)
                item.setExpanded(True)
                return active, "发送{}指令{}".format(name, category)
        if direction == "RX":
            if command == Command.ACK and len(payload) == 2:
                original, sequence = payload
                if active is not None and (active.command, active.sequence) == (original, sequence):
                    active.confirmed = True
                    self._set_status(active, "已确认")
                    return active, "下位机已确认{}指令".format(ACTION_NAMES[original])
                return None, "确认回包 #{} · 无匹配轮次".format(sequence)
            if command == Command.ACTION_DONE and len(payload) == 1:
                sequence = payload[0]
                if active is not None and active.confirmed and active.sequence == sequence:
                    self._set_status(active, "已完成")
                    self._active = None
                    if active.command == Command.WASTE_RESULT:
                        self._last_sort = active, time.monotonic()
                    return active, "{}动作完成".format(ACTION_NAMES[active.command])
                return None, "完成回包 #{} · 无匹配轮次".format(sequence)
            if command == Command.BIN_FULL and len(payload) == 1:
                description = "{}桶已满 · 状态通知".format(WASTE_NAMES.get(payload[0], "未知类别"))
                if self._last_sort is not None:
                    last_sort, completed = self._last_sort
                    if last_sort.waste == payload[0] and time.monotonic() - completed <= 1.5:
                        self._last_sort = None
                        self._set_status(last_sort, "已完成 · 桶已满")
                        return last_sort, description
                return None, description
            if command == Command.ERROR:
                return None, "下位机错误 · " + payload.decode("utf-8", errors="replace")
            if command == Command.ACK and payload == bytes([Command.HEARTBEAT]):
                return None, "下位机已确认心跳"
        if command == Command.HEARTBEAT:
            return None, "心跳"
        return None, "命令 0x{:02X} · 无法解析载荷".format(command)
