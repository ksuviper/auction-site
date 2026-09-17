from django.http import HttpResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.utils.text import Truncator

from allauth.account.views import LoginView as AllauthLoginView
from django_ratelimit.decorators import ratelimit

from auctions.models import HomePageStep, SitePage

# How much of the About Us page to show on the homepage before offering a link
# to the rest. Words rather than characters so the cut lands between them.
ABOUT_EXCERPT_WORDS = 70


def index(request):
    """
    The homepage: how the site works, and who runs it.

    It used to list active auctions. Browsing is category → seller → that
    seller's plants now, and the category sidebar beside this content is the
    way in, so a second wall of listings here was a competing route to the same
    place.

    Every word on it is admin-editable. The welcome and the heading above the
    steps are SitePage rows, the steps themselves are HomePageStep rows, and
    About Us is read from the page behind /about/ so this section and that one
    cannot drift apart. Each lookup can come back empty — a fresh database, a
    row an admin deleted — and the template falls back to the wording the page
    shipped with rather than rendering a gap.
    """
    about_page = SitePage.objects.filter(slug='about-us').first()
    home_page = SitePage.objects.filter(slug='home').first()
    steps_page = SitePage.objects.filter(slug='home-how-it-works').first()
    steps = list(HomePageStep.objects.filter(is_published=True))

    excerpt = ''
    truncated = False
    if about_page is not None:
        # html=True keeps the markup balanced when the cut lands inside a tag.
        excerpt = Truncator(about_page.body).words(
            ABOUT_EXCERPT_WORDS, html=True, truncate=' …'
        )
        # Compared against the whole body rather than counting words, so the
        # "Read more" link appears only when something was actually left out.
        truncated = excerpt != about_page.body

    return render(request, 'index.html', {
        'home_page': home_page,
        'steps_page': steps_page,
        'steps': steps,
        'about_page': about_page,
        'about_excerpt': excerpt,
        'about_truncated': truncated,
    })


@method_decorator(
    ratelimit(key='ip', rate='5/m', method='POST', block=False),
    name='post',
)
class RateLimitedLoginView(AllauthLoginView):
    def post(self, request, *args, **kwargs):
        if getattr(request, 'limited', False):
            return HttpResponse(
                'Too many login attempts. Please wait a minute and try again.',
                status=429,
            )
        return super().post(request, *args, **kwargs)
