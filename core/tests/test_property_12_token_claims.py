"""Feature: gym-saas-core, Property 12."""
import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from rest_framework_simplejwt.tokens import AccessToken

from core.services.auth_tokens import issue_tokens
from core.tests import factories

pytestmark = pytest.mark.django_db


def names_user(decoded, user):
    """Whether the token's user claim identifies this user.

    Compared on `str()` of both sides rather than by equality on the raw claim.
    simplejwt serialises the user-id claim as an integer up to 5.3 and as a string
    from 5.4 onwards, and the property under test is that the token *names this
    user* - not which JSON scalar type the library happened to choose. The lookup
    path agrees: `auth_tokens.refresh_tokens` passes the claim straight to
    `filter(pk=...)`, which coerces either form.
    """
    return str(decoded["user_id"]) == str(user.pk)


# Feature: gym-saas-core, Property 12: For any User identifier, role, and Gym
# identifier, decoding the access token issued for those values yields exactly those
# three values.
# Validates: Requirements 13.7, 13.2
@hyp_settings(max_examples=100)
@given(role=st.sampled_from(["owner", "trainer", "member"]))
def test_access_token_claims_round_trip(role):
    gym = factories.make_gym()
    profile = {
        "owner": factories.make_owner,
        "trainer": factories.make_trainer,
        "member": factories.make_member,
    }[role](gym)
    user = profile.user

    tokens = issue_tokens(user)
    decoded = AccessToken(tokens["access"])

    assert names_user(decoded, user)
    assert decoded["role"] == role
    assert decoded["gym_id"] == gym.pk


@hyp_settings(max_examples=100)
@given(role=st.sampled_from(["owner", "trainer", "member"]))
def test_refresh_token_carries_the_same_claims(role):
    from rest_framework_simplejwt.tokens import RefreshToken

    gym = factories.make_gym()
    profile = {
        "owner": factories.make_owner,
        "trainer": factories.make_trainer,
        "member": factories.make_member,
    }[role](gym)

    tokens = issue_tokens(profile.user)
    decoded = RefreshToken(tokens["refresh"])

    assert names_user(decoded, profile.user)
    assert decoded["role"] == role
    assert decoded["gym_id"] == gym.pk


def test_staff_account_token_carries_a_null_gym():
    """D6: a platform operator holds no profile and therefore no Gym."""
    staff = factories.make_staff()
    decoded = AccessToken(issue_tokens(staff)["access"])

    assert decoded["gym_id"] is None
    assert names_user(decoded, staff)
