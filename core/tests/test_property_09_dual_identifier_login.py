"""Feature: gym-saas-core, Property 9."""
import pytest
from django.urls import reverse
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from core.tests import factories
from core.tests.strategies import e164_phones

pytestmark = pytest.mark.django_db


# Feature: gym-saas-core, Property 9: For any User with a password and for any
# identifier kind in {email, phone}, presenting that identifier with the correct
# password authenticates exactly that User and yields both a signed access token and a
# signed refresh token.
# Validates: Requirements 10.4, 10.5, 13.1
@hyp_settings(max_examples=100)
@given(
    kind=st.sampled_from(["email", "phone", "email_upper", "email_mixed"]),
    phone=e164_phones(),
)
def test_login_succeeds_for_either_identifier(kind, phone, api_client):
    from django.core.cache import cache

    cache.clear()
    from core.models import User

    User.objects.all().delete()
    gym = factories.make_gym()
    password = "Correct-Horse-Battery-7"
    profile = factories.make_owner(gym, password=password)
    user = profile.user
    user.phone = phone
    user.save(update_fields=["phone"])

    identifier = {
        "email": user.email,
        "email_upper": user.email.upper(),
        "email_mixed": user.email.capitalize(),
        "phone": phone,
    }[kind]

    response = api_client.post(
        reverse("core:login"), {"identifier": identifier, "password": password}, format="json"
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["access"] and body["refresh"]

    # Both tokens verify against the signing key, and name exactly this user.
    # Compared on `str()` of both sides: simplejwt serialises the user-id claim as an
    # integer up to 5.3 and as a string from 5.4 on, and what this property asserts is
    # that the token names this user, not which JSON scalar type the library picked.
    access = AccessToken(body["access"])
    refresh = RefreshToken(body["refresh"])
    assert str(access["user_id"]) == str(user.pk)
    assert str(refresh["user_id"]) == str(user.pk)


@hyp_settings(max_examples=100)
@given(phone=e164_phones())
def test_phone_login_resolves_the_same_user_as_email_login(phone, api_client):
    from django.core.cache import cache

    cache.clear()
    from core.models import User

    User.objects.all().delete()
    gym = factories.make_gym()
    password = "Correct-Horse-Battery-7"
    user = factories.make_owner(gym, password=password).user
    user.phone = phone
    user.save(update_fields=["phone"])

    by_email = api_client.post(
        reverse("core:login"), {"identifier": user.email, "password": password}, format="json"
    )
    by_phone = api_client.post(
        reverse("core:login"), {"identifier": phone, "password": password}, format="json"
    )

    assert by_email.status_code == by_phone.status_code == 200
    assert AccessToken(by_email.json()["access"])["user_id"] == (
        AccessToken(by_phone.json()["access"])["user_id"]
    )
