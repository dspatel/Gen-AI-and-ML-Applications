from websocket import create_connection
import json
import pandas

#~m~55~m~{m: "chart_create_session", p: ["cs_zLCqlT5LX9LF", ""]}


socket = "wss://prodata.tradingview.com/socket.io/websocket"

ws = create_connection(socket)



def create_msg(ws,fun,arg):
    ms = json.dumps({"m":fun,"p":arg})
    msg = "~m~{}~m~{}".format(str(len(ms)),ms)
    ws.send (msg)


create_msg(ws,"chart_create_session", ["cs_zLCqlT5LX9LF", ""])
create_msg(ws,"resolve_symbol", ["cs_zLCqlT5LX9LF","sds_sym_1","={\"adjustment\":\"splits\",\"currency-id\":\"USD\",\"metric\":\"price\",\"session\":\"regular\",\"symbol\":\"NASDAQ:NVDA\"}"])
create_msg(ws,"create_series", ["cs_zLCqlT5LX9LF","sds_1","s1","sds_sym_1","5",
                                10,""])


def format_data(data):
    start = data.find('"s":[')
    end = data.find('],"ns"')
    final_data = json.loads(data[start+5:end])
    print(final_data)

while True:
    res = ws.recv()
    print(res)
    print("/n-----")
    if "series_completed" in res:
        break

    

