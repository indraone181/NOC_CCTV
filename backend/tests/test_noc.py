import os
import requests
import pytest
from dotenv import dotenv_values

BASE_URL = dotenv_values('/app/frontend/.env')['REACT_APP_BACKEND_URL'].strip().rstrip('/')

ADMIN_EMAIL = 'test-admin-noc@example.com'
ADMIN_PASS = 'TestAdmin123!'
OPERATOR_EMAIL = 'test-operator-noc@example.com'
OPERATOR_PASS = 'TestOperator123!'


@pytest.fixture(scope='session')
def admin_client():
    s = requests.Session()
    status = s.get(f'{BASE_URL}/api/setup/status').json()
    if status.get('needs_setup'):
        r = s.post(f'{BASE_URL}/api/setup', json={'name': 'TEST Admin', 'email': ADMIN_EMAIL, 'password': ADMIN_PASS})
        assert r.status_code == 200, r.text
    else:
        r = s.post(f'{BASE_URL}/api/auth/login', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASS})
        if r.status_code != 200:
            pytest.skip('Cannot login as test admin; DB has other users. Drop users collection to re-run.')
    return s


@pytest.fixture(scope='session')
def operator_client(admin_client):
    admin_client.post(f'{BASE_URL}/api/users', json={'name': 'TEST Operator', 'email': OPERATOR_EMAIL, 'password': OPERATOR_PASS, 'role': 'operator'})
    s = requests.Session()
    r = s.post(f'{BASE_URL}/api/auth/login', json={'email': OPERATOR_EMAIL, 'password': OPERATOR_PASS})
    assert r.status_code == 200, r.text
    return s


# Setup endpoints
def test_setup_status_after_admin(admin_client):
    r = requests.get(f'{BASE_URL}/api/setup/status')
    assert r.status_code == 200
    assert r.json() == {'needs_setup': False}


def test_setup_conflict_when_users_exist():
    r = requests.post(f'{BASE_URL}/api/setup', json={'name': 'X', 'email': 'x@x.com', 'password': 'password123'})
    assert r.status_code == 409


# Auth flows
def test_login_wrong_password():
    r = requests.post(f'{BASE_URL}/api/auth/login', json={'email': ADMIN_EMAIL, 'password': 'WrongPassword1'})
    assert r.status_code == 401


def test_me_unauthenticated():
    assert requests.get(f'{BASE_URL}/api/auth/me').status_code == 401


def test_login_me_logout_flow(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/auth/me')
    assert r.status_code == 200
    body = r.json()
    assert body['role'] == 'admin'
    assert body['email'] == ADMIN_EMAIL
    # cookie exists
    assert 'access_token' in admin_client.cookies.get_dict()


def test_logout_and_relogin(admin_client):
    s = requests.Session()
    r = s.post(f'{BASE_URL}/api/auth/login', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASS})
    assert r.status_code == 200
    assert s.post(f'{BASE_URL}/api/auth/logout').status_code == 200
    assert s.get(f'{BASE_URL}/api/auth/me').status_code == 401


# Cameras
def test_list_cameras(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/cameras')
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    for cam in data:
        assert '_id' not in cam  # ObjectId must not leak


def test_create_camera_no_objectid_leak(admin_client):
    payload = {'name': 'TEST ObjectId Camera', 'ip': '127.0.0.1', 'nvr': 'TEST-NVR', 'location': 'TEST', 'picture_url': 'http://example.com/p.jpg'}
    r = admin_client.post(f'{BASE_URL}/api/cameras', json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert '_id' not in body, f'ObjectId leak: {body}'
    assert body['name'] == payload['name']
    assert body['ip'] == payload['ip']
    assert body['status'] == 'unknown'
    assert 'id' in body and isinstance(body['id'], str)
    cid = body['id']

    # GET verifies persistence and clean serialization
    listed = admin_client.get(f'{BASE_URL}/api/cameras').json()
    found = next((c for c in listed if c['id'] == cid), None)
    assert found is not None
    assert '_id' not in found

    # Update
    r = admin_client.put(f'{BASE_URL}/api/cameras/{cid}', json={**payload, 'name': 'TEST Updated'})
    assert r.status_code == 200
    assert r.json()['name'] == 'TEST Updated'
    assert '_id' not in r.json()

    # Delete
    r = admin_client.delete(f'{BASE_URL}/api/cameras/{cid}')
    assert r.status_code == 200
    listed = admin_client.get(f'{BASE_URL}/api/cameras').json()
    assert not any(c['id'] == cid for c in listed)


def test_cameras_refresh_updates_history(admin_client):
    before = admin_client.get(f'{BASE_URL}/api/reports/history').json()
    r = admin_client.post(f'{BASE_URL}/api/cameras/refresh', timeout=30)
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    for item in items:
        assert item['status'] in ('online', 'offline')
        assert '_id' not in item
    after = admin_client.get(f'{BASE_URL}/api/reports/history').json()
    assert len(after) >= len(before)
    if after:
        latest = after[-1]
        assert {'online', 'offline', 'total', 'availability', 'checked_at'} <= set(latest.keys())


# Ping
def test_ping_offline(admin_client):
    r = admin_client.post(f'{BASE_URL}/api/ping', json={'ip': '127.0.0.1', 'port': 1}, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body['status'] == 'offline'
    assert body['ip'] == '127.0.0.1'


# Reports
def test_reports_summary(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/reports/summary')
    assert r.status_code == 200
    body = r.json()
    assert {'total', 'online', 'offline', 'availability'} <= body.keys()
    assert body['total'] == body['online'] + body['offline']


def test_reports_history(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/reports/history')
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_reports_export_csv(admin_client):
    r = admin_client.get(f'{BASE_URL}/api/reports/export')
    assert r.status_code == 200, r.text
    assert 'text/csv' in r.headers.get('content-type', '')
    assert 'name,ip' in r.text


# Operator permissions
def test_operator_can_read_cameras(operator_client):
    assert operator_client.get(f'{BASE_URL}/api/cameras').status_code == 200


def test_operator_cannot_create_camera(operator_client):
    r = operator_client.post(f'{BASE_URL}/api/cameras', json={'name': 'x', 'ip': '127.0.0.1', 'nvr': 'x'})
    assert r.status_code == 403


def test_operator_cannot_delete_camera(admin_client, operator_client):
    # admin creates a camera to attempt delete
    r = admin_client.post(f'{BASE_URL}/api/cameras', json={'name': 'TEST Op-Delete', 'ip': '127.0.0.1', 'nvr': 'x'})
    cid = r.json()['id']
    try:
        assert operator_client.delete(f'{BASE_URL}/api/cameras/{cid}').status_code == 403
    finally:
        admin_client.delete(f'{BASE_URL}/api/cameras/{cid}')


def test_operator_cannot_list_users(operator_client):
    assert operator_client.get(f'{BASE_URL}/api/users').status_code == 403


def test_operator_cannot_create_user(operator_client):
    r = operator_client.post(f'{BASE_URL}/api/users', json={'name': 'x', 'email': 'y@y.com', 'password': 'password123', 'role': 'operator'})
    assert r.status_code == 403


def test_operator_can_ping(operator_client):
    r = operator_client.post(f'{BASE_URL}/api/ping', json={'ip': '127.0.0.1', 'port': 1}, timeout=10)
    assert r.status_code == 200
    assert r.json()['status'] == 'offline'
