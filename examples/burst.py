"""A bus burst with a long idle squeezed out, markers and a tri-state line."""

from waveview import Diagram

d = Diagram(title="突发传输（省略空闲期）", theme="dark")
clk = d.clock("clk", period="10ns")
rst_n = d.wire("rst_n", init=0)
en = d.wire("en", init=0)
sda = d.wire("sda", init="z")
addr = d.bus("addr", init="x", width=16, label="addr[15:0]")

d.at("12ns")(rst_n=1)
d.posedge(clk)(en=1, addr=0x1000, sda=0)
d.marker(label="T0")
for i in range(1, 4):
    d.posedge(clk)(addr=0x1000 + 4 * i, sda=i % 2)
d.posedge(clk)(en=0, addr="x", sda="z")

d.gap("1us")
d.posedge(clk)(en=1, addr=0x2000, sda=1)
d.marker(label="T1")
d.posedge(clk, 2)(en=0, addr="x", sda="z")

if __name__ == "__main__":
    print(d.describe())
    d.save("out/burst.png")
