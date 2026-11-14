"""SPI mode 0 with a gated SCLK and warn_noop=False."""

from waveview import Diagram

TX, RX = 0xA5, 0x3C

d = Diagram(title="SPI 模式 0（门控时钟）", warn_noop=False)
cs_n = d.wire("cs_n", init=1, label="CS_n")
sclk = d.clock("sclk", period="50ns", phase="45ns", idle=0, gated=True, label="SCLK")
mosi = d.wire("mosi", init="z", label="MOSI")
miso = d.wire("miso", init="z", label="MISO")
bit = d.bus("bit", init="z", label="当前位")

cs_fall = d.at("20ns")(cs_n=0, mosi=(TX >> 7) & 1, miso=(RX >> 7) & 1, bit="b7")
sclk.enable()

first_rise = d.posedge(sclk)
for i in range(1, 8):
    d.negedge(sclk)(
        mosi=(TX >> (7 - i)) & 1,
        miso=(RX >> (7 - i)) & 1,
        bit=f"b{7 - i}",
    )

end = d.negedge(sclk)(mosi="z", miso="z", bit="z")
sclk.disable()
d.delay("25ns")(cs_n=1)

d.measure(cs_fall, first_rise, "tCSS")
d.region(sclk, cs_fall, end, "8 个时钟")

if __name__ == "__main__":
    print(d.describe())
    d.save("out/gated_spi.png")
