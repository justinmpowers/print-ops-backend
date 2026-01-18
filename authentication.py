import os
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

class EtsyOAuth:
    """Handle Etsy 3-legged OAuth authentication"""
    
    ETSY_AUTH_URL = 'https://www.etsy.com/oauth/connect'
    ETSY_TOKEN_URL = 'https://api.etsy.com/v3/public/oauth/token'
    ETSY_USER_URL = 'https://api.etsy.com/v3/application/users/me'
    ETSY_SHOP_URL = 'https://api.etsy.com/v3/application/shops'
    
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
            'scope': 'transactions_r shops_r email_r',  # Added email_r and shops_r
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
            'client_secret': current_app.config['ETSY_CLIENT_SECRET'],
            'redirect_uri': current_app.config['ETSY_REDIRECT_URI'],
            'code': code
        }
        
        if code_verifier:
            data['code_verifier'] = code_verifier
            print(f"DEBUG: Using PKCE code_verifier")
        else:
            print(f"DEBUG: No PKCE code_verifier provided")
        
        try:
            print(f"DEBUG: Posting to Etsy token URL: {EtsyOAuth.ETSY_TOKEN_URL}")
            response = requests.post(EtsyOAuth.ETSY_TOKEN_URL, data=data)
            print(f"DEBUG: Etsy response status: {response.status_code}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: Request exception: {str(e)}")
            raise Exception(f"Failed to exchange code for token: {str(e)}")
    
    @staticmethod
    def get_user_info(access_token):
        """Get authenticated user info from Etsy"""
        headers = {
            'Authorization': f'Bearer {access_token}',
            'x-api-key': current_app.config['ETSY_CLIENT_ID']
        }
        
        try:
            print(f"DEBUG: Getting user info from {EtsyOAuth.ETSY_USER_URL}")
            response = requests.get(EtsyOAuth.ETSY_USER_URL, headers=headers)
            print(f"DEBUG: User info response status: {response.status_code}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: User info request exception: {str(e)}")
            raise Exception(f"Failed to get user info: {str(e)}")
    
    @staticmethod
    def get_shop_info(access_token, shop_id):
        """Get shop information from Etsy"""
        headers = {
            'Authorization': f'Bearer {access_token}',
            'x-api-key': current_app.config['ETSY_CLIENT_ID']
        }
        
        try:
            url = f"{EtsyOAuth.ETSY_SHOP_URL}/{shop_id}"
            print(f"DEBUG: Getting shop info from {url}")
            response = requests.get(url, headers=headers)
            print(f"DEBUG: Shop info response status: {response.status_code}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"DEBUG: Shop info request exception: {str(e)}")
            raise Exception(f"Failed to get shop info: {str(e)}")
    
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
            response = requests.post(EtsyOAuth.ETSY_TOKEN_URL, data=data)
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