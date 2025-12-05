"""Protobuf decoder for Yahoo Finance PricingData messages."""
import struct
from datetime import datetime, timezone
from typing import Union

from common.models.market_hours import MarketHours
from common.models.pricing_data import PricingData


# Type alias for field values from protobuf parsing
FieldValue = Union[bytes, int]


class PricingDataDecoder:
    """Decoder for Yahoo Finance protobuf PricingData messages.
    
    Uses raw wire-format decoding for maximum performance.
    Field numbers match the quotefeeder.proto schema.
    """
    
    # Wire types
    WIRE_VARINT: int = 0
    WIRE_FIXED64: int = 1
    WIRE_LENGTH_DELIMITED: int = 2
    WIRE_FIXED32: int = 5

    def decode(self, data: bytes) -> PricingData:
        """Decode protobuf bytes into PricingData.
        
        Args:
            data: Raw protobuf bytes.
            
        Returns:
            Decoded PricingData instance.
        """
        fields: dict[int, FieldValue] = self._parse_fields(data)
        
        # Extract timestamp and convert to datetime
        time_ms: int = self._decode_sint64(fields.get(3, 0))
        timestamp: datetime = datetime.fromtimestamp(
            time_ms / 1000.0, 
            tz=timezone.utc
        )
        
        return PricingData(
            id=self._decode_string(fields.get(1, b"")),
            price=self._decode_float(fields.get(2, b"")),
            time=timestamp,
            currency=self._decode_string(fields.get(4, b"")),
            exchange=self._decode_string(fields.get(5, b"")),
            market_hours=MarketHours(self._to_int(fields.get(7, 1))),
            change=self._decode_float(fields.get(12, b"")),
            change_percent=self._decode_float(fields.get(8, b"")),
            day_volume=self._decode_sint64(fields.get(9, 0)),
            day_high=self._decode_float(fields.get(10, b"")),
            day_low=self._decode_float(fields.get(11, b"")),
            open_price=self._decode_float(fields.get(15, b"")),
            previous_close=self._decode_float(fields.get(16, b"")),
            bid=self._decode_float(fields.get(23, b"")),
            bid_size=self._decode_sint64(fields.get(24, 0)),
            ask=self._decode_float(fields.get(25, b"")),
            ask_size=self._decode_sint64(fields.get(26, 0)),
            last_size=self._decode_sint64(fields.get(22, 0)),
            short_name=self._decode_string(fields.get(13, b"")),
        )

    def _parse_fields(self, data: bytes) -> dict[int, FieldValue]:
        """Parse protobuf wire format into field dictionary.
        
        Args:
            data: Raw protobuf bytes.
            
        Returns:
            Dictionary mapping field numbers to values.
        """
        fields: dict[int, FieldValue] = {}
        pos: int = 0
        length: int = len(data)
        
        while pos < length:
            tag, pos = self._read_varint(data, pos)
            field_number: int = tag >> 3
            wire_type: int = tag & 0x07
            
            if wire_type == self.WIRE_VARINT:
                value, pos = self._read_varint(data, pos)
                fields[field_number] = value
            elif wire_type == self.WIRE_FIXED64:
                fields[field_number] = data[pos:pos + 8]
                pos += 8
            elif wire_type == self.WIRE_LENGTH_DELIMITED:
                str_len, pos = self._read_varint(data, pos)
                fields[field_number] = data[pos:pos + str_len]
                pos += str_len
            elif wire_type == self.WIRE_FIXED32:
                fields[field_number] = data[pos:pos + 4]
                pos += 4
            else:
                break
                
        return fields

    def _read_varint(self, data: bytes, pos: int) -> tuple[int, int]:
        """Read a varint from the buffer.
        
        Args:
            data: Raw bytes buffer.
            pos: Current position in buffer.
            
        Returns:
            Tuple of (decoded value, new position).
        """
        result: int = 0
        shift: int = 0
        
        while pos < len(data):
            byte: int = data[pos]
            pos += 1
            result |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
            
        return result, pos

    def _to_int(self, value: FieldValue) -> int:
        """Convert field value to int.
        
        Args:
            value: Field value (bytes or int).
            
        Returns:
            Integer value.
        """
        if isinstance(value, int):
            return value
        return 0

    def _decode_sint64(self, value: FieldValue) -> int:
        """Decode a signed 64-bit integer (zigzag encoded).
        
        Args:
            value: Varint value or 0.
            
        Returns:
            Decoded signed integer.
        """
        if isinstance(value, int):
            return (value >> 1) ^ -(value & 1)
        return 0

    def _decode_float(self, value: FieldValue) -> float:
        """Decode a 32-bit float from fixed32 bytes.
        
        Args:
            value: 4 bytes or 0.
            
        Returns:
            Decoded float value.
        """
        if isinstance(value, bytes) and len(value) == 4:
            return struct.unpack("<f", value)[0]
        return 0.0

    def _decode_string(self, value: FieldValue) -> str:
        """Decode a UTF-8 string.
        
        Args:
            value: Raw bytes or empty.
            
        Returns:
            Decoded string.
        """
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return ""
