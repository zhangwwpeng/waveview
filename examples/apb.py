"""APB3: one write with a wait state, then one read without."""

from waveview import Diagram

d = Diagram(title="APB 写 / 读传输（50MHz）", theme="bamboo")
clk = d.clock("clk", freq="50MHz")
rst_n = d.wire("rst_n", init=0)

with d.group("控制"):
    psel = d.wire("psel", init=0)
    penable = d.wire("penable", init=0)
    pwrite = d.wire("pwrite", init=0)

with d.group("地址与数据"):
    paddr = d.bus("paddr", init="x", width=32, label="paddr[31:0]")
    pwdata = d.bus("pwdata", init="x", width=32, label="pwdata[31:0]")
    prdata = d.bus("prdata", init="x", width=32, label="prdata[31:0]")

with d.group("响应"):
    pready = d.wire("pready", init=0)
    pslverr = d.wire("pslverr", init=0)

d.at("5ns")(rst_n=1)
d.marker(at="5ns", label="复位释放")

# 写：setup -> access -> 一个等待周期 -> 完成
e_sel = d.posedge(clk)(psel=1, pwrite=1, paddr=0x20, pwdata=0x12345678)
e_en = d.posedge(clk)(penable=1)
e_rdy = d.posedge(clk)(pready=1)
e_done = d.posedge(clk)(psel=0, penable=0, pwrite=0, paddr="x", pwdata="x", pready=0)
d.marker(label="写完成")

# 读：setup -> access，无等待
e_rsel = d.posedge(clk)(psel=1, paddr=0x24)
e_ren = d.posedge(clk)(penable=1, pready=1, prdata=0xDEADBEEF)
e_rdone = d.posedge(clk)(psel=0, penable=0, paddr="x", prdata="x", pready=0)
d.marker(label="读完成")

d.region(penable, e_en, e_rdy, "等待 PREADY")
d.arrow(e_en.penable, e_rdy.pready, "从机插等待")
d.measure(e_sel.psel, e_done.psel, "写传输")
d.measure(e_rsel.psel, e_rdone.psel, "读传输")

if __name__ == "__main__":
    print(d.describe())
    d.save("out/apb.svg")
    d.save("out/apb.png", scale=3)
