"""上位机通信协议骨架。

帧格式请阅读仓库根目录的 ``PROTOCOL.md``。本文件中的两个 TODO 是通信
模块的核心考核点：封包和流式拆包。请勿假设一次 ``recv`` 恰好得到一帧。
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import List


HEADER = b"\xAA\x55"
MAX_BODY_LENGTH = 64


class Command(IntEnum):
    WASTE_RESULT = 0x01
    ACK = 0x02
    ACTION_DONE = 0x03
    BIN_FULL = 0x04
    START_COMPRESS = 0x05
    HEARTBEAT = 0x06
    ERROR = 0x7F


class WasteType(IntEnum):
    HAZARDOUS = 0x01
    OTHER = 0x02
    RECYCLABLE = 0x03
    KITCHEN = 0x04


WASTE_NAMES = {
    WasteType.RECYCLABLE: "可回收垃圾",
    WasteType.KITCHEN: "厨余垃圾",
    WasteType.HAZARDOUS: "有害垃圾",
    WasteType.OTHER: "其他垃圾",
}


@dataclass(frozen=True)
class Packet:
    command: Command
    payload: bytes = b""

    def encode(self) -> bytes:
        """TODO 1：按协议生成完整二进制帧。

        需要完成长度检查、LENGTH 字段和 CHECKSUM 计算。
        """
        PAYLOAD = self.payload
        COMMAND = bytes([self.command])
        length = 1 + len(self.payload)
        # 长度校验
        if length > MAX_BODY_LENGTH:
            raise ValueError
        
        LENGTH = bytes([length])
        CHECKSUM = bytes([(length + sum(PAYLOAD + COMMAND)) & 0xFF])
        return HEADER + LENGTH + COMMAND + PAYLOAD + CHECKSUM


class StreamParser:
    """把任意分片的 TCP/串口字节流增量解析为 Packet。"""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.error_count = 0

    def reset(self) -> None:
        self._buffer.clear()
        self.error_count = 0

    def feed(self, data: bytes) -> List[Packet]:
        """TODO 2：支持半包、粘包、噪声和错误校验后的重新同步。"""
        PacketList = []
        #将data存入缓存
        self._buffer += data
        while True:
            # 遍历缓存，删除HEADER前噪声
            noiseIndex = 0
            for index, byte in enumerate(self._buffer):
                if(byte == 0xAA and self._buffer[index+1] == 0x55 if index < len(self._buffer) - 1 else True):
                    break
                noiseIndex += 1
            del self._buffer[:noiseIndex]

            if len(self._buffer) > 2:
                length = self._buffer[2]
                if length > 64:
                    self.error_count += 1
                    del self._buffer[:2]
                    continue
                if len(self._buffer) >= length + 4:
                    if sum(self._buffer[2:3+length]) & 0xFF == self._buffer[3+length]:
                        #校验通过
                        COMMAND = self._buffer[3]
                        PAYLOAD = bytes(self._buffer[4:3+length])
                        PacketList.append(Packet(COMMAND, PAYLOAD))          
                        del self._buffer[:4+length]
                        continue
                    else:
                        #校验不通过
                        self.error_count += 1
                        del self._buffer[:2]
                        continue
                else:
                    break 
            else:
                break
        return PacketList


