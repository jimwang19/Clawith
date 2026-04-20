import urllib.request, json, sys

url = 'http://127.0.0.1:8000/api/auth/login'
data = json.dumps({"login_identifier": "jim", "password": "wrongpass"}).encode()
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
try:
    resp = urllib.request.urlopen(req)
    print('STATUS:', resp.status, resp.read().decode()[:200])
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode()[:300])
except Exception as e:
    print('ERROR:', e)
