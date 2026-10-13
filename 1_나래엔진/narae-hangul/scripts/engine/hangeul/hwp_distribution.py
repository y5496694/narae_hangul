"""Read HWP distribution streams without removing their restrictions.

Original implementation of Hancom's published distribution-document format:
https://cdn.hancom.com/link/docs/한글문서파일형식_배포용문서_revision1.2.pdf
본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
"""
import struct


def decode_view_stream(data):
    """Return the compressed/plain view records and distribution option bits."""
    from Crypto.Cipher import AES
    if len(data) < 260:
        raise ValueError("HWP 배포용 본문 스트림이 불완전합니다.")
    header, = struct.unpack_from("<I", data)
    if header & 1023 != 28 or header >> 20 != 256:
        raise ValueError("HWP 배포용 본문 키 레코드가 올바르지 않습니다.")
    encoded = data[4:260]
    seed, = struct.unpack_from("<I", encoded)
    state, mask = seed, bytearray()
    def random_value():
        nonlocal state
        state = (state * 214013 + 2531011) & 0xFFFFFFFF
        return (state >> 16) & 0x7FFF
    while len(mask) < 256:
        value, repeat = random_value() & 255, (random_value() & 15) + 1
        mask.extend(bytes([value]) * min(repeat, 256 - len(mask)))
    decoded = bytes(value ^ mask[index] for index, value in enumerate(encoded))
    offset = (seed & 15) + 4
    key = decoded[offset:offset + 16]
    options, = struct.unpack_from("<H", decoded, offset + 80)
    payload = data[260:]
    if not payload or len(payload) % 16:
        raise ValueError("HWP 배포용 본문 AES 블록 길이가 올바르지 않습니다.")
    return AES.new(key, AES.MODE_ECB).decrypt(payload), options
