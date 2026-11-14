"""Single clock: a valid/ready handshake."""

from waveview import Diagram

d = Diagram(title="valid/ready 握手")
clk = d.clock("clk", freq="100MHz")
valid = d.wire("valid", init=0)
ready = d.wire("ready", init=0)
data = d.bus("data", init="x", width=8, label="data[7:0]")

d.posedge(clk)
e1 = d.posedge(clk)(valid=1, data=0xA5)
e2 = d.posedge(clk, 2)(ready=1)
d.posedge(clk)(valid=0, ready=0, data="x")

d.arrow(e1.valid, e2.ready, "2 拍后响应")

if __name__ == "__main__":
    print(d.describe())
    d.save("out/handshake.svg")
    d.save("out/handshake.png")
