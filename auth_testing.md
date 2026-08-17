# Authentication testing
- First run: GET /api/setup/status, POST /api/setup with name/email/password.
- Login: POST /api/auth/login, then GET /api/auth/me with cookie.
- Verify operator gets 403 on POST/PUT/DELETE camera and user routes.
- Verify admin can add users and manage cameras.