"""SPI mode 0: send 0xA5 on MOSI while the slave answers 0x3C on MISO.

SCLK only runs during the transfer, so it is a wire toggled every half period.
"""

from waveview import Diagram

TX, RX = 0xA5, 0x3C
HALF = "25ns"

d = Diagram(title="SPI 模式 0：发送 0xA5 / 接收 0x3C")
cs_n = d.wire("cs_n", init=1, label="CS_n")
sclk = d.wire("sclk", init=0, label="SCLK")
mosi = d.wire("mosi", init="z", label="MOSI")
miso = d.wire("miso", init="z", label="MISO")
bit = d.bus("bit", init="z", label="当前位")


def bits(byte: int, i: int) -> int:
    return (byte >> (7 - i)) & 1


# CS_n falls and the first bit goes out half a period before the first rising edge.
start = d.at("20ns")(cs_n=0, mosi=bits(TX, 0), miso=bits(RX, 0), bit="b7")
first_rise = d.delay(HALF)(sclk=1)  # both sides sample on the rising edge
for i in range(1, 8):
    # Shift the next bit out on the falling edge, only where it changes.
    nxt = {"sclk": 0, "bit": f"b{7 - i}"}
    if bits(TX, i) != bits(TX, i - 1):
        nxt["mosi"] = bits(TX, i)
    if bits(RX, i) != bits(RX, i - 1):
        nxt["miso"] = bits(RX, i)
    d.delay(HALF)(**nxt)
    d.delay(HALF)(sclk=1)
end = d.delay(HALF)(sclk=0, mosi="z", miso="z", bit="z")
d.delay(HALF)(cs_n=1)

d.measure(start, first_rise, "tCSS")
d.arrow(start.mosi, first_rise.sclk, "采样 b7")
d.region(sclk, start, end, "8 个时钟")

if __name__ == "__main__":
    print(d.describe())
    d.save("out/spi.png")
