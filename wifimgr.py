# original repository https://github.com/tayfunulu/WiFiManager
import machine
import network
import socket
import time
import ubinascii
import ure
from sys import platform
from esp_micro.config_loader import read_profiles
from esp_micro.config_loader import write_profiles
from esp_micro.config_loader import write_mqtt

NETWORK_PROFILES = 'wifi.dat'
RP2 = platform == 'rp2'
AUTH_MODE = {0: 'open', 1: 'WEP', 2: 'WPA-PSK', 3: 'WPA2-PSK', 4: 'WPA/WPA2-PSK'}
unique_id = ubinascii.hexlify(machine.unique_id()).decode('utf-8')
ap_ssid = f'WifiManager_{unique_id}'
ap_password = 'admin'
ap_auth_mode = 3  # WPA2
wlan_ap = network.WLAN(network.AP_IF)
wlan_sta = network.WLAN(network.STA_IF)
server_socket = None


def get_connection():
    """Return a working WLAN(STA_IF) instance or None"""
    # First check if there is already any connection:
    if wlan_sta.isconnected():
        return wlan_sta
    connected = False
    try:
        # ESP connecting to Wi-Fi takes time, wait for a bit and try again:
        time.sleep(3)
        if wlan_sta.isconnected():
            return wlan_sta
        # Read known network profiles from file
        profiles = read_profiles()
        # Search Wi-Fis in range
        wlan_sta.active(True)
        networks = wlan_sta.scan()
        for ssid, bssid, channel, rssi, auth_mode, hidden in sorted(networks, key=lambda x: x[3], reverse=True):
            ssid = ssid.decode('utf-8')
            encrypted = auth_mode > 0
            print(f'''ssid: {ssid} chan: {channel} rssi: {rssi} authmode: {AUTH_MODE.get(auth_mode, '?')}''')
            if encrypted:
                if ssid in profiles:
                    password = profiles[ssid]
                    connected = do_connect(ssid, password)
                else:
                    print('skipping unknown encrypted network')
            if connected:
                break
    except OSError as e:
        print(f'exception: {e}')
    # start web server for connection manager:
    if not connected:
        connected = start()
    return wlan_sta if connected else None


def do_connect(ssid, password):
    wlan_sta.active(True)
    if wlan_sta.isconnected():
        return None
    print(f'Trying to connect to {ssid}({password})...')
    wlan_sta.connect(ssid, password)
    connected = None
    for retry in range(100):
        connected = wlan_sta.isconnected()
        if connected:
            break
        time.sleep(0.1)
        print('.', end='')
    if connected:
        print(f'\nConnected. Network config: {wlan_sta.ifconfig()}')
    else:
        print(f'\nFailed. Not Connected to: {ssid}')
    return connected


def send_header(client, status_code=200, content_length=None):
    client.sendall(f'HTTP/1.0 {status_code} OK\r\n')
    client.sendall('Content-Type: text/html\r\n')
    if content_length is not None:
        client.sendall(f'Content-Length: {content_length}\r\n')
    client.sendall('\r\n')


def send_response(client, payload, status_code=200):
    content_length = len(payload)
    send_header(client, status_code, content_length)
    if content_length > 0:
        client.sendall(payload)
    client.close()


def handle_root(client):
    wlan_sta.active(True)
    ssids = sorted(ssid.decode('utf-8') for ssid, *_ in wlan_sta.scan())
    send_header(client)
    client.sendall('''\
        <html>
            <h1 style="color: #5e9ca0; text-align: center;">
                <span style="color: #ff0000;">
                    Wi-Fi Client Setup
                </span>
            </h1>
            <form action="configure" method="post">
                <table style="margin-left: auto; margin-right: auto;">
                    <tbody>
    ''')
    while len(ssids):
        ssid = ssids.pop(0)
        client.sendall(f'''
                        <tr>
                            <td colspan="2">
                                <input type="radio" name="ssid" value="{ssid}" />{ssid}
                            </td>
                        </tr>
        ''')
    client.sendall(f'''
                        <tr>
                            <td>Password:</td>
                            <td><input name="password" type="password" /></td>
                        </tr>
                        <tr>
                            <td>MQTT server:</td>
                            <td><input name="mqttServer" type="text" /></td>
                        </tr>
                        <tr>
                            <td>MQTT user:</td>
                            <td><input name="mqttUser" type="text" /></td>
                        </tr>
                        <tr>
                            <td>MQTT password:</td>
                            <td><input name="mqttPassword" type="password" /></td>
                        </tr>
                        <tr>
                            <td>Github Repo:</td>
                            <td><input name="githubRepo" type="text" /></td>
                        </tr>
                        <tr>
                            <td>install new versions automatically</td>
                            <td><input type="checkbox" id="autoUpdate" name="autoUpdate" checked></td>
                        </tr>
                        <tr>
                            <td>install development versions</td>
                            <td><input type="checkbox" id="unstableVersions" name="unstableVersions"></td>
                        </tr>
                    </tbody>
                </table>
                <p style="text-align: center;">
                    <input type="submit" value="Submit" />
                </p>
            </form>
            <p>&nbsp;</p>
            <hr />
            <h5>
                <span style="color: #ff0000;">
                    Your ssid and password information will be saved into the
                    "{NETWORK_PROFILES}" file in your ESP module for future usage.
                    Be careful about security!
                </span>
            </h5>
            <hr />
            <h2 style="color: #2e6c80;">
                Some useful infos:
            </h2>
            <ul>
                <li>
                    Original code from <a href="https://github.com/cpopp/MicroPythonSamples"
                        target="_blank" rel="noopener">cpopp/MicroPythonSamples</a>.
                </li>
                <li>
                    This code available at <a href="https://github.com/EugeneRymarev/WiFiManager"
                        target="_blank" rel="noopener">EugeneRymarev/WiFiManager</a>.
                </li>
            </ul>
        </html>
    ''')
    client.close()


def handle_configure(client, request):
    def replace_marks(s):
        return s.replace('%3F', '?').replace('%21', '!')

    def decode_utf_8(s):
        return s.decode('utf-8')

    def decode_and_replace(s):
        return replace_marks(decode_utf_8(s))

    regexp = \
        'ssid=([^&]*)&password=([^&]*)&mqttServer=([^&]*)&mqttUser=([^&]*)&mqttPassword=([^&]*)&githubRepo=([^&]*)(.*)'
    match = ure.search(regexp, request)
    if match is None:
        send_response(client, 'Parameters not found', status_code=400)
        return False
    # version 1.9 compatibility
    mqtt_server = None
    mqtt_user = None
    mqtt_password = None
    github_repo = None
    auto_update = None
    unstable_versions = None
    try:
        ssid = decode_and_replace(match.group(1))
        password = decode_and_replace(match.group(2))
        mqtt_server = decode_and_replace(match.group(3))
        mqtt_user = decode_and_replace(match.group(4))
        mqtt_password = decode_and_replace(match.group(5))
        github_repo = decode_and_replace(match.group(6)).replace('%3A', ':').replace('%2F', '/')
        rest = decode_and_replace(match.group(7))
        auto_update = 'autoUpdate' in rest
        unstable_versions = 'unstableVersions' in rest
        print(f'mqttServer: {mqtt_server}')
        print(f'mqttUser: {mqtt_user}')
        print(f'mqttPassword: {mqtt_password}')
        if auto_update:
            print('autoUpdate!')
        if unstable_versions:
            print('unstableVersions!')
    except Exception:
        ssid = replace_marks(match.group(1))
        password = replace_marks(match.group(2))
    if len(ssid) == 0:
        send_response(client, 'SSID must be provided', status_code=400)
        return False
    if do_connect(ssid, password):
        response = f'''
            <html>
                <center>
                    <br><br>
                    <h1 style="color: #5e9ca0; text-align: center;">
                        <span style="color: #ff0000;">
                            ESP successfully connected to WiFi network {ssid}.
                        </span>
                    </h1>
                    <br><br>
                </center>
            </html>
        '''
        send_response(client, response)
        try:
            profiles = read_profiles()
        except OSError:
            profiles = {}
        profiles[ssid] = password
        write_profiles(profiles)
        write_mqtt(mqtt_server, mqtt_user, mqtt_password, github_repo, auto_update, unstable_versions)
        time.sleep(5)
        machine.reset()
        return True
    else:
        response = f'''
            <html>
                <center>
                    <h1 style="color: #5e9ca0; text-align: center;">
                        <span style="color: #ff0000;">
                            ESP could not connect to WiFi network {ssid}.
                        </span>
                    </h1>
                    <br><br>
                    <form>
                        <input type="button" value="Go back!" onclick="history.back()"></input>
                    </form>
                </center>
            </html>
        '''
        send_response(client, response)
        return False


def handle_not_found(client, url):
    send_response(client, f'Path not found: {url}', status_code=404)


def stop():
    global server_socket
    if server_socket:
        server_socket.close()
        server_socket = None


def start(port=80):
    global server_socket
    addr = socket.getaddrinfo('0.0.0.0', port)[0][-1]
    stop()
    if RP2:
        wlan_ap.config(essid=ap_ssid, password=ap_password)
    else:
        wlan_ap.config(essid=ap_ssid, password=ap_password, authmode=ap_auth_mode)
    wlan_sta.active(True)
    wlan_ap.active(True)
    server_socket = socket.socket()
    server_socket.bind(addr)
    server_socket.listen(1)
    print(f'Connect to WiFi ssid {ap_ssid}, default password: {ap_password}')
    print('and access the ESP via your favorite web browser at 192.168.4.1.')
    print(f'Listening on: {addr}')
    while True:
        if wlan_sta.isconnected():
            return True
        client, addr = server_socket.accept()
        print(f'client connected from {addr}')
        try:
            client.settimeout(5.0)
            request = b''
            try:
                while '\r\n\r\n' not in request:
                    request += client.recv(512)
            except OSError:
                pass
            print(f'Request is: {request}')
            if 'HTTP' not in request:  # skip invalid requests
                continue
            # version 1.9 compatibility
            try:
                regexp = '(?:GET|POST) /(.*?)(?:\\?.*?)? HTTP'
                url = ure.search(regexp, request).group(1).decode('utf-8').rstrip('/')
            except Exception:
                regexp = '(?:GET|POST) /(.*?)(?:\\?.*?)? HTTP'
                url = ure.search(regexp, request).group(1).rstrip('/')
            print(f'URL is {url}')
            if url == '':
                handle_root(client)
            elif url == 'configure':
                handle_configure(client, request)
            else:
                handle_not_found(client, url)
        finally:
            client.close()
