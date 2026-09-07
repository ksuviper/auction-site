"""
URL configuration for auction_site project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.views import serve as serve_static
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView

from auctions.views import RateLimitedSignupView

from . import views

urlpatterns = [
    path('', views.index, name='home'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    # The privacy policy is admin-editable now (a SitePage at
    # /legal/privacy-policy/, seeded by migration 0020 with the text that used
    # to live in legal/privacy_policy.html). This keeps the old address and URL
    # name working: it is what the Google and Facebook OAuth apps were given,
    # and what any existing link points at.
    path(
        'privacy/',
        RedirectView.as_view(url='/legal/privacy-policy/', permanent=True),
        name='privacy_policy',
    ),
    # Data deletion stays a static page. It documents a specific procedure
    # rather than prose the client would edit, and Facebook requires the URL.
    path('privacy/data-deletion/', TemplateView.as_view(template_name='legal/data_deletion.html'), name='data_deletion'),
    path('favicon.ico', RedirectView.as_view(url=settings.STATIC_URL + 'favicon.ico', permanent=True)),
    path('admin/invoices/', include('auctions.invoice_urls')),
    path('admin/combined-invoices/', include('auctions.combined_invoice_urls')),
    path('admin/weekly-setup/', include('auctions.weekly_setup_urls')),
    path('admin/reports/', include('auctions.reports_urls')),
    path('admin/', admin.site.urls),
    # Rate-limited login/signup must come before include('allauth.urls') so they
    # match first — allauth registers the same URL names, and the resolver keeps
    # the first pattern it finds.
    path('accounts/login/', views.RateLimitedLoginView.as_view(), name='account_login'),
    path('accounts/signup/', RateLimitedSignupView.as_view(), name='account_signup'),
    path('accounts/', include('allauth.urls')),
    path('', include('auctions.subscription_urls')),
    path('', include('auctions.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
