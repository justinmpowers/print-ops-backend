import os
import logging
import requests
import jwt
import secrets
import hashlib
import base64
from functools import wraps
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from flask import current_app, request, jsonify, session
from models import db, User

logger = logging.getLogger(__name__)

class EtsyOAuth:
    """Handle Etsy 3-legged OAuth authentication"""

    ETSY_AUTH_URL = 'https://www.etsy.com/oauth/connect'
    ETSY_TOKEN_URL = 'https://api.etsy.com/v3/public/oauth/token'
    ETSY_USER_URL = 'https://api.etsy.com/v3/application/users/{user_id}'
    ETSY_SHOPS_URL = 'https://api.etsy.com/v3/application/shops'
    
    @staticmethod
    def get_authorization_url():
        """Generate Etsy OAuth authorization URL with PKCE"""
        # Generate state parameter for CSRF protection
        state = secrets.token_urlsafe(32)
        
        # Generate PKCE code_verifier and code_challenge
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')
        
        params = {
            'response_type': 'code',
            'client_id': current_app.config['ETSY_CLIENT_ID'],
            'redirect_uri': current_app.config['ETSY_REDIRECT_URI'],
            'scope': 'transactions_r shops_r email_r profile_r',
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }
        
        # Store state in session for verification
        session['oauth_state'] = state
        
        return f"{EtsyOAuth.ETSY_AUTH_URL}?{urlencode(params)}", state, code_verifier
    
    @staticmethod
    def exchange_code_for_token(code, code_verifier=None):
        """Exchange authorization code for access token with PKCE"""
        data = {
            'grant_type': 'authorization_code',
            'client_id': current_app.config['ETSY_CLIENT_ID'],
            'redirect_uri': current_app.config['ETSY_REDIRECT_URI'],
            'code': code
        }

        if code_verifier:
            # PKCE flow: code_verifier replaces client_secret
            data['code_verifier'] = code_verifier
        else:
            data['client_secret'] = current_app.config['ETSY_CLIENT_SECRET']

        try:
            response = requests.post(EtsyOAuth.ETSY_TOKEN_URL, data=data)
            response.raise_for_status()
            token_data = response.json()

            # Etsy's token response includes user_id but it can be null.
            # The documented method (per Etsy's own quickstart) is to extract
            # the numeric prefix from the access token: "{user_id}.{opaque}"
            access_token = token_data['access_token']
            user_id = access_token.split('.')[0]
            if not user_id.isdigit():
                raise Exception(f"Could not extract user_id from access token prefix: {user_id!r}")
            token_data['user_id'] = user_id

            return token_data
        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to exchange code for token: {str(e)}")
    
    @staticmethod
    def get_user_profile(access_token, user_id):
        """Get profile info (first_name, login_name) for the authenticated user.

        Requires profile_r scope. May 403 on draft/unverified Etsy apps — callers
        must treat this as optional and not depend on it for core functionality.
        """
        url = EtsyOAuth.ETSY_USER_URL.format(user_id=user_id)
        response = requests.get(url, headers={
            'Authorization': f'Bearer {access_token}',
            'x-api-key': current_app.config['ETSY_CLIENT_ID']
        })
        if not response.ok:
            logger.warning(f"[get_user_profile] {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
        return response.json()

    @staticmethod
    def get_shop_for_user(user_id):
        """Return the first Etsy shop owned by user_id.

        Uses the public findShops endpoint which requires only the API key —
        no OAuth bearer token. This works for all apps including draft/unverified.
        Raises on network failure; returns None when the user has no shops.
        """
        response = requests.get(
            EtsyOAuth.ETSY_SHOPS_URL,
            headers={'x-api-key': current_app.config['ETSY_CLIENT_ID']},
            params={'user_id': user_id, 'limit': 1},
            timeout=10
        )
        if not response.ok:
            logger.error(f"[get_shop_for_user] {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
        results = response.json().get('results', [])
        return results[0] if results else None

    @staticmethod
    def refresh_access_token(refresh_token):
        """Refresh an expired access token"""
        data = {
            'grant_type': 'refresh_token',
            'client_id': current_app.config['ETSY_CLIENT_ID'],
            'client_secret': current_app.config['ETSY_CLIENT_SECRET'],
            'refresh_token': refresh_token
        }
        
        try:
            response = requests.post(
                EtsyOAuth.ETSY_TOKEN_URL,
                data=data,
                timeout=current_app.config.get('HTTP_TIMEOUT', 10)
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to refresh token: {str(e)}")

class TokenManager:
    """Manage JWT tokens for session management"""
    
    @staticmethod
    def create_token(user_id, expires_in_hours=None):
        """Create a JWT token for the user"""
        if expires_in_hours is None:
            expires_in_hours = current_app.config.get('JWT_EXPIRATION_HOURS', 24)
        
        payload = {
            'user_id': user_id,
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(hours=expires_in_hours)
        }
        
        token = jwt.encode(
            payload,
            current_app.config['SECRET_KEY'],
            algorithm='HS256'
        )
        return token
    
    @staticmethod
    def verify_token(token):
        """Verify and decode JWT token"""
        try:
            payload = jwt.decode(
                token,
                current_app.config['SECRET_KEY'],
                algorithms=['HS256']
            )
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

def token_required(f):
    """Decorator to require valid JWT token"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                return jsonify({'message': 'Invalid token format'}), 401
        
        if not token:
            return jsonify({'message': 'Token is missing'}), 401
        
        payload = TokenManager.verify_token(token)
        if not payload:
            return jsonify({'message': 'Invalid or expired token'}), 401
        
        user = User.query.get(payload['user_id'])
        
        if not user:
            return jsonify({'message': 'User not found'}), 404
        
        request.user = user
        return f(*args, **kwargs)
    
    return decorated