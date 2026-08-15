# scratchpad/redeploy.py — needs PORTAINER_URL / PORTAINER_TOKEN in env
import json
import os
import urllib.request

URL, TOK = os.environ["PORTAINER_URL"], os.environ["PORTAINER_TOKEN"]
H = {"X-API-Key": TOK, "Content-Type": "application/json"}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    return urllib.request.urlopen(
        urllib.request.Request(URL + path, data=data, headers=H, method=method)
    )


st = next(
    s
    for s in json.load(call("GET", "/api/stacks"))
    if s["Name"] == "discord-bot" and s["EndpointId"] == 6
)
sid = st["Id"]
compose = json.load(call("GET", f"/api/stacks/{sid}/file"))["StackFileContent"]
env = [{"name": e["name"], "value": e["value"]} for e in st["Env"]]
body = {"stackFileContent": compose, "env": env, "prune": False, "pullImage": True}
print("redeploy status:", call("PUT", f"/api/stacks/{sid}?endpointId=6", body).status, "stack", sid)
