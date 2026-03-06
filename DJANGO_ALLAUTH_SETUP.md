# Django-AllAuth Setup Guide

## Overview
Your auction site is now configured with **django-allauth** for authentication and multi-factor authentication (MFA).

## Installation Summary

### Installed Packages
- `django-allauth[mfa]` - Includes optional dependencies for MFA (TOTP, WebAuthn, etc.)

### Configuration Applied

1. **Updated `settings.py`**:
   - Added to `INSTALLED_APPS`:
     - `django.contrib.sites`
     - `allauth`
     - `allauth.account`
     - `allauth.socialaccount`
     - `allauth.mfa`
   
   - Added authentication backend:
     - `allauth.account.auth_backends.AuthenticationBackend`
   
   - Added middleware:
     - `allauth.account.middleware.AccountMiddleware`
   
   - Configured account settings:
     - Login methods: email and username
     - Email verification: optional
     - Session remember: enabled
     - Built-in signup fields: email, username, password

2. **Updated `urls.py`**:
   - Added route: `path('accounts/', include('allauth.urls'))`
   - All auth-related URLs are now at `/accounts/`

3. **Ran migrations**:
   - Created all necessary database tables for allauth

## Available URLs

After setup, these URLs are automatically available:

### Account Management
- `/accounts/signup/` - User registration
- `/accounts/login/` - User login
- `/accounts/logout/` - User logout
- `/accounts/email/` - Manage email addresses
- `/accounts/password/change/` - Change password
- `/accounts/password/reset/` - Reset password

### MFA (2FA)
- `/accounts/2fa/` - MFA management panel
- `/accounts/2fa/activate/authenticator/` - Setup TOTP authenticator
- `/accounts/2fa/deactivate/authenticator/` - Disable TOTP
- `/accounts/2fa/backup-tokens/` - View backup codes
- `/accounts/2fa/totp/` - TOTP management

## Usage Examples

### Enable MFA for a User

Users can enable TOTP (Time-based One-Time Password) authentication by:
1. Logging in
2. Visiting `/accounts/2fa/`
3. Clicking "Setup Authenticator"
4. Scanning the QR code with an authenticator app (Google Authenticator, Authy, Microsoft Authenticator, etc.)
5. Entering the code to confirm

### Accessing User Info in Templates

```django
{% if user.is_authenticated %}
  Welcome, {{ user.username }}!
  {% if user.emailaddress_set.exists %}
    Email: {{ user.emailaddress_set.first.email }}
  {% endif %}
{% else %}
  <a href="{% url 'account_login' %}">Login</a>
{% endif %}
```

### Custom Views with Login Required

```python
from django.contrib.auth.decorators import login_required

@login_required
def my_view(request):
    # This view requires user to be logged in
    return render(request, 'template.html')
```

## Next Steps

1. **Customize Templates** (Optional):
   - Create custom templates in `auction_site/templates/account/` to override default allauth templates
   - Default templates are in the allauth package (auto-discovered via `APP_DIRS: True`)

2. **Add Social Authentication** (Optional):
   - Uncomment social providers in `INSTALLED_APPS`:
     - `allauth.socialaccount.providers.google`
     - `allauth.socialaccount.providers.github`
     - etc.
   - Configure OAuth credentials in Django admin

3. **Email Configuration** (For Production):
   - Currently set to console backend (emails printed to console)
   - Update `EMAIL_BACKEND` in settings for production:
     ```python
     EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
     EMAIL_HOST = 'smtp.gmail.com'
     EMAIL_PORT = 587
     EMAIL_USE_TLS = True
     EMAIL_HOST_USER = 'your-email@gmail.com'
     EMAIL_HOST_PASSWORD = 'your-app-password'
     ```

4. **Customize Signup Fields** (If Needed):
   - Modify `ACCOUNT_SIGNUP_FIELDS` in settings.py to add/remove fields
   - Fields ending with `*` are required

## Important Settings

```python
# Login methods
ACCOUNT_LOGIN_METHODS = {'email', 'username'}

# Email verification
ACCOUNT_EMAIL_VERIFICATION = 'optional'  # or 'mandatory' or 'none'

# Session
ACCOUNT_SESSION_REMEMBER = True  # Remember login on browser close

# MFA
MFA_SUPPORTED_AUTH_FORMS = [
    'allauth.mfa.totp.forms.ActivateTOTPForm',  # TOTP authenticator
]

# Redirects after login
LOGIN_REDIRECT_URL = '/'
ACCOUNT_LOGOUT_REDIRECT_URL = '/'
```

## Troubleshooting

### "Missing SITE_ID"
- ✅ Already configured in settings.py: `SITE_ID = 1`

### Email not sending
- Check `EMAIL_BACKEND` setting
- For development, use `console.EmailBackend` (emails print to console)

### TOTP not working
- Ensure user's authenticator app is synced with server time
- Check database for `allauth.mfa.models.Authenticator` records

### Permission denied errors
- Ensure `'django.contrib.auth'` and `'django.contrib.contenttypes'` are in `INSTALLED_APPS`
- These are already included in your setup

## Resources
- [django-allauth Documentation](https://django-allauth.readthedocs.io/)
- [TOTP Implementation](https://django-allauth.readthedocs.io/en/latest/mfa/)
- [Customizing AllAuth Templates](https://django-allauth.readthedocs.io/en/latest/templates/)
