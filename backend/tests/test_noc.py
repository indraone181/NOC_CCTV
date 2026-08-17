import requests
import pytest

BASE_URL = open('/app/frontend/.env').readline().split('=', 1)[1].strip()

@pytest.fixture(scope='session')
def admin_client():
    s = requests.Session()
    if s.get(f'{BASE_URL}/api/setup/status').json().get('needs_setup'):
        r = s.post(f'{BASE_URL}/api/setup', json={'name':'TEST Admin','email':'test-admin-noc@example.com','password':'TestAdmin123!'})
        assert r.status_code == 200, r.text
    else:
        r = s.post(f'{BASE_URL}/api/auth/login', json={'email':'test-admin-noc@example.com','password':'TestAdmin123!'})
        if r.status_code != 200:
            pytest.skip('Existing setup uses credentials unavailable in empty test_credentials.md')
    return s

@pytest.fixture
def operator_client(admin_client):
    email='test-operator-noc@example.com'
    r=admin_client.post(f'{BASE_URL}/api/users', json={'name':'TEST Operator','email':email,'password':'TestOperator123!','role':'operator'})
    assert r.status_code in (200,409), r.text
    s=requests.Session(); r=s.post(f'{BASE_URL}/api/auth/login',json={'email':email,'password':'TestOperator123!'})
    assert r.status_code == 200, r.text
    return s

# Authentication and protected session flows
def test_setup_login_me_logout(admin_client):
    r=admin_client.get(f'{BASE_URL}/api/auth/me'); assert r.status_code == 200; assert r.json()['role']=='admin'
    r=admin_client.post(f'{BASE_URL}/api/auth/logout'); assert r.status_code == 200
    assert admin_client.get(f'{BASE_URL}/api/auth/me').status_code == 401
    r=admin_client.post(f'{BASE_URL}/api/auth/login',json={'email':'test-admin-noc@example.com','password':'TestAdmin123!'}); assert r.status_code==200

# Dashboard, refresh, reporting and CSV flows
def test_dashboard_refresh_reports_export(admin_client):
    r=admin_client.get(f'{BASE_URL}/api/cameras'); assert r.status_code==200; assert len(r.json())>=5
    r=admin_client.post(f'{BASE_URL}/api/cameras/refresh',timeout=30); assert r.status_code==200; assert all(c['status'] in ('online','offline') for c in r.json())
    r=admin_client.get(f'{BASE_URL}/api/reports/summary'); assert r.status_code==200; assert {'total','online','offline','availability'} <= r.json().keys()
    r=admin_client.get(f'{BASE_URL}/api/reports/export'); assert r.status_code==200; assert 'text/csv' in r.headers.get('content-type',''); assert 'name,ip' in r.text

# Admin device CRUD persistence
def test_admin_camera_crud(admin_client):
    payload={'name':'TEST Camera','ip':'127.0.0.1','nvr':'TEST-NVR','location':'TEST'}
    r=admin_client.post(f'{BASE_URL}/api/cameras',json=payload); assert r.status_code==200; cid=r.json()['id']
    try:
        r=admin_client.get(f'{BASE_URL}/api/cameras'); assert any(c['id']==cid for c in r.json())
        r=admin_client.put(f'{BASE_URL}/api/cameras/{cid}',json={**payload,'name':'TEST Camera Updated'}); assert r.status_code==200; assert r.json()['name']=='TEST Camera Updated'
    finally:
        r=admin_client.delete(f'{BASE_URL}/api/cameras/{cid}'); assert r.status_code==200

# Operator permissions and network probe
def test_operator_read_only_and_probe(operator_client):
    assert operator_client.get(f'{BASE_URL}/api/cameras').status_code==200
    assert operator_client.post(f'{BASE_URL}/api/cameras',json={'name':'x','ip':'127.0.0.1','nvr':'x'}).status_code==403
    r=operator_client.post(f'{BASE_URL}/api/ping',json={'ip':'127.0.0.1','port':1},timeout=10); assert r.status_code==200; assert r.json()['status']=='offline'
    assert operator_client.get(f'{BASE_URL}/api/users').status_code==403