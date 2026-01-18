from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    """User model to store Etsy user information"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    etsy_user_id = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(255))  # NEW
    shop_id = db.Column(db.String(50))
    shop_name = db.Column(db.String(255))  # NEW
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text)
    token_expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    filaments = db.relationship('Filament', backref='user', lazy=True, cascade='all, delete-orphan')
    orders = db.relationship('Order', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'etsy_user_id': self.etsy_user_id,
            'username': self.username,
            'first_name': self.first_name,  # NEW
            'shop_id': self.shop_id,
            'shop_name': self.shop_name,  # NEW
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

class Filament(db.Model):
    """Filament inventory tracking"""
    __tablename__ = 'filaments'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    color = db.Column(db.String(255), nullable=False)
    material = db.Column(db.String(255), nullable=False)  # PLA, ABS, PETG, etc.
    initial_amount = db.Column(db.Float, nullable=False)  # grams
    current_amount = db.Column(db.Float, nullable=False)  # grams
    unit = db.Column(db.String(50), default='g')  # g for grams
    cost_per_gram = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    usage_logs = db.relationship('FilamentUsage', backref='filament', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'color': self.color,
            'material': self.material,
            'initial_amount': self.initial_amount,
            'current_amount': self.current_amount,
            'unit': self.unit,
            'cost_per_gram': self.cost_per_gram,
            'used_amount': self.initial_amount - self.current_amount,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

class FilamentUsage(db.Model):
    """Track filament usage per print/order"""
    __tablename__ = 'filament_usage'
    
    id = db.Column(db.Integer, primary_key=True)
    filament_id = db.Column(db.Integer, db.ForeignKey('filaments.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'))
    amount_used = db.Column(db.Float, nullable=False)  # grams
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'filament_id': self.filament_id,
            'order_id': self.order_id,
            'amount_used': self.amount_used,
            'description': self.description,
            'created_at': self.created_at.isoformat()
        }

class Order(db.Model):
    """Etsy orders"""
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    etsy_order_id = db.Column(db.String(255), unique=True, nullable=False)
    etsy_shop_id = db.Column(db.String(255), nullable=False)
    buyer_email = db.Column(db.String(255))
    buyer_name = db.Column(db.String(255))
    total_amount = db.Column(db.Float)
    currency = db.Column(db.String(10))
    status = db.Column(db.String(50))  # PENDING, SHIPPED, DELIVERED, CANCELED, etc.
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)
    shipped_at = db.Column(db.DateTime)
    
    # Filament tracking
    filament_assigned = db.Column(db.Boolean, default=False)
    total_filament_used = db.Column(db.Float, default=0)  # grams
    
    # Relationships
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    filament_usage = db.relationship('FilamentUsage', backref='order', lazy=True)
    
    # Local tracking
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'etsy_order_id': self.etsy_order_id,
            'etsy_shop_id': self.etsy_shop_id,
            'buyer_email': self.buyer_email,
            'buyer_name': self.buyer_name,
            'total_amount': self.total_amount,
            'currency': self.currency,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'shipped_at': self.shipped_at.isoformat() if self.shipped_at else None,
            'filament_assigned': self.filament_assigned,
            'total_filament_used': self.total_filament_used,
            'items': [item.to_dict() for item in self.items],
            'synced_at': self.synced_at.isoformat()
        }

class OrderItem(db.Model):
    """Individual items in an Etsy order"""
    __tablename__ = 'order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    etsy_listing_id = db.Column(db.String(255))
    title = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    price = db.Column(db.Float)
    
    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'etsy_listing_id': self.etsy_listing_id,
            'title': self.title,
            'quantity': self.quantity,
            'price': self.price
        }
