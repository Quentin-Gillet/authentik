"""passbook core authentication views"""
from logging import getLogger
from typing import Dict

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.forms.utils import ErrorList
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, reverse
from django.utils.translation import ugettext as _
from django.views import View
from django.views.generic import FormView

from passbook.core.auth.view import AuthenticationView
from passbook.core.exceptions import PasswordPolicyInvalid
from passbook.core.forms.authentication import LoginForm, SignUpForm
from passbook.core.models import Invitation, Nonce, Source, User
from passbook.core.signals import invitation_used, user_signed_up
from passbook.core.tasks import send_email
from passbook.lib.config import CONFIG

LOGGER = getLogger(__name__)


class LoginView(UserPassesTestMixin, FormView):
    """Allow users to sign in"""

    template_name = 'login/form.html'
    form_class = LoginForm
    success_url = '.'

    # Allow only not authenticated users to login
    def test_func(self):
        return self.request.user.is_authenticated is False

    def handle_no_permission(self):
        if 'next' in self.request.GET:
            return redirect(self.request.GET.get('next'))
        return redirect(reverse('passbook_core:overview'))

    def get_context_data(self, **kwargs):
        kwargs['config'] = CONFIG.get('passbook')
        kwargs['is_login'] = True
        kwargs['title'] = _('Log in to your account')
        kwargs['primary_action'] = _('Log in')
        kwargs['show_sign_up_notice'] = CONFIG.y('passbook.sign_up.enabled')
        kwargs['sources'] = []
        sources = Source.objects.filter(enabled=True).select_subclasses()
        if any(source.is_link for source in sources):
            for source in sources:
                kwargs['sources'].append(source.get_login_button)
            self.template_name = 'login/with_sources.html'
        return super().get_context_data(**kwargs)

    def get_user(self, uid_value) -> User:
        """Find user instance. Returns None if no user was found."""
        for search_field in CONFIG.y('passbook.uid_fields'):
            # Workaround for E-Mail -> email
            if search_field == 'e-mail':
                search_field = 'email'
            users = User.objects.filter(**{search_field: uid_value})
            if users.exists():
                LOGGER.debug("Found user %s with uid_field %s", users.first(), search_field)
                return users.first()
        return None

    def form_valid(self, form: LoginForm) -> HttpResponse:
        """Form data is valid"""
        pre_user = self.get_user(form.cleaned_data.get('uid_field'))
        if not pre_user:
            # No user found
            return self.invalid_login(self.request)
        self.request.session.flush()
        self.request.session[AuthenticationView.SESSION_PENDING_USER] = pre_user.pk
        return redirect(reverse('passbook_core:auth-process'))

    def invalid_login(self, request: HttpRequest, disabled_user: User = None) -> HttpResponse:
        """Handle login for disabled users/invalid login attempts"""
        messages.error(request, _('Failed to authenticate.'))
        return self.render_to_response(self.get_context_data())


class LogoutView(LoginRequiredMixin, View):
    """Log current user out"""

    def dispatch(self, request):
        """Log current user out"""
        logout(request)
        messages.success(request, _("You've successfully been logged out."))
        return redirect(reverse('passbook_core:auth-login'))


class SignUpView(UserPassesTestMixin, FormView):
    """Sign up new user, optionally consume one-use invitation link."""

    template_name = 'login/form.html'
    form_class = SignUpForm
    success_url = '.'
    # Invitation insatnce, if invitation link was used
    _invitation = None
    # Instance of newly created user
    _user = None

    # Allow only not authenticated users to login
    def test_func(self):
        return self.request.user.is_authenticated is False

    def handle_no_permission(self):
        return redirect(reverse('passbook_core:overview'))

    def dispatch(self, request, *args, **kwargs):
        """Check if sign-up is enabled or invitation link given"""
        allowed = False
        if 'invitation' in request.GET:
            invitations = Invitation.objects.filter(uuid=request.GET.get('invitation'))
            allowed = invitations.exists()
            if allowed:
                self._invitation = invitations.first()
        if CONFIG.y('passbook.sign_up.enabled'):
            allowed = True
        if not allowed:
            messages.error(request, _('Sign-ups are currently disabled.'))
            return redirect(reverse('passbook_core:auth-login'))
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        if self._invitation:
            initial = {}
            if self._invitation.fixed_username:
                initial['username'] = self._invitation.fixed_username
            if self._invitation.fixed_email:
                initial['email'] = self._invitation.fixed_email
            return initial
        return super().get_initial()

    def get_context_data(self, **kwargs):
        kwargs['config'] = CONFIG.get('passbook')
        kwargs['is_login'] = True
        kwargs['title'] = _('Sign Up')
        kwargs['primary_action'] = _('Sign up')
        return super().get_context_data(**kwargs)

    def form_valid(self, form: SignUpForm) -> HttpResponse:
        """Create user"""
        try:
            self._user = SignUpView.create_user(form.cleaned_data, self.request)
        except PasswordPolicyInvalid as exc:
            # Manually inject error into form
            # pylint: disable=protected-access
            errors = form._errors.setdefault("password", ErrorList())
            for error in exc.messages:
                errors.append(error)
            return self.form_invalid(form)
        needs_confirmation = True
        if self._invitation and not self._invitation.needs_confirmation:
            needs_confirmation = False
        if needs_confirmation:
            nonce = Nonce.objects.create(user=self._user)
            LOGGER.debug(str(nonce.uuid))
            # Send email to user
            send_email.delay(self._user.email, _('Confirm your account.'),
                             'email/account_confirm.html', {
                                 'url': self.request.build_absolute_uri(
                                     reverse('passbook_core:auth-sign-up-confirm', kwargs={
                                         'nonce': nonce.uuid
                                     })
                                 )
                             })
            self._user.is_active = False
            self._user.save()
        self.consume_invitation()
        messages.success(self.request, _("Successfully signed up!"))
        LOGGER.debug("Successfully signed up %s",
                     form.cleaned_data.get('email'))
        return redirect(reverse('passbook_core:auth-login'))

    def consume_invitation(self):
        """Consume invitation if an invitation was used"""
        if self._invitation:
            invitation_used.send(
                sender=self,
                request=self.request,
                invitation=self._invitation,
                user=self._user)
            self._invitation.delete()

    @staticmethod
    def create_user(data: Dict, request: HttpRequest = None) -> User:
        """Create user from data

        Args:
            data: Dictionary as returned by SignUpForm's cleaned_data
            request: Optional current request.

        Returns:
            The user created

        Raises:
            PasswordPolicyInvalid: if any policy are not fulfilled.
                                   This also deletes the created user.
        """
        # Create user
        new_user = User.objects.create(
            username=data.get('username'),
            email=data.get('email'),
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
        )
        new_user.is_active = True
        try:
            new_user.set_password(data.get('password'))
            new_user.save()
            request.user = new_user
            # Send signal for other auth sources
            user_signed_up.send(
                sender=SignUpView,
                user=new_user,
                request=request)
            return new_user
        except PasswordPolicyInvalid as exc:
            new_user.delete()
            raise exc


class SignUpConfirmView(View):
    """Confirm registration from Nonce"""

    def get(self, request, nonce):
        """Verify UUID and activate user"""
        nonce = get_object_or_404(Nonce, uuid=nonce)
        nonce.user.is_active = True
        nonce.user.save()
        # Workaround: hardcoded reference to ModelBackend, needs testing
        nonce.user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(request, nonce.user)
        nonce.delete()
        messages.success(request, _('Successfully confirmed registration.'))
        return redirect('passbook_core:overview')


class PasswordResetView(View):
    """Temporarily authenticate User and allow them to reset their password"""

    def get(self, request, nonce):
        """Authenticate user with nonce and redirect to password change view"""
        # 3. (Optional) Trap user in password change view
        nonce = get_object_or_404(Nonce, uuid=nonce)
        # Workaround: hardcoded reference to ModelBackend, needs testing
        nonce.user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(request, nonce.user)
        nonce.delete()
        messages.success(request, _(('Temporarily authenticated with Nonce, '
                                     'please change your password')))
        return redirect('passbook_core:user-change-password')
