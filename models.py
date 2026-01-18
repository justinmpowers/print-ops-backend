from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

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
    printers = db.relationship('Printer', backref='user', lazy=True, cascade='all, delete-orphan')
    
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


class Customer(db.Model):
    """CRM customer profile"""
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255))
    email = db.Column(db.String(255), index=True)
    phone = db.Column(db.String(50))
    notes = db.Column(db.Text)
    first_order_at = db.Column(db.DateTime)
    last_order_at = db.Column(db.DateTime)
    order_count = db.Column(db.Integer, default=0)
    total_spend = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    orders = db.relationship('Order', backref='customer', lazy=True)
    requests = db.relationship('CustomerRequest', backref='customer', lazy=True, cascade='all, delete-orphan')
    feedback = db.relationship('CustomerFeedback', backref='customer', lazy=True, cascade='all, delete-orphan')

    def segment(self):
        if self.total_spend is not None and self.total_spend >= 300:
            return 'VIP'
        if self.order_count is not None and self.order_count >= 2:
            return 'repeat'
        if self.order_count == 1:
            return 'new'
        return 'prospect'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'notes': self.notes,
            'first_order_at': self.first_order_at.isoformat() if self.first_order_at else None,
            'last_order_at': self.last_order_at.isoformat() if self.last_order_at else None,
            'order_count': self.order_count,
            'total_spend': self.total_spend,
            'segment': self.segment(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
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
    low_stock_threshold = db.Column(db.Float, default=100.0)  # Alert when below this amount
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
            'low_stock_threshold': self.low_stock_threshold,
            'is_low_stock': self.current_amount <= self.low_stock_threshold,
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


class Printer(db.Model):
    """Registered printers for multi-printer support"""
    __tablename__ = 'printers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    model = db.Column(db.String(255))
    location = db.Column(db.String(255))
    status = db.Column(db.String(50), default='IDLE')  # IDLE, PRINTING, MAINTENANCE
    notes = db.Column(db.Text)
    maintenance_interval_days = db.Column(db.Integer, default=30)
    last_maintenance_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    orders = db.relationship('Order', backref='printer', lazy=True)

    def next_maintenance_due(self):
        if not self.last_maintenance_at or not self.maintenance_interval_days:
            return None
        return self.last_maintenance_at + timedelta(days=self.maintenance_interval_days)

    def to_dict(self):
        next_due = self.next_maintenance_due()
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'model': self.model,
            'location': self.location,
            'status': self.status,
            'notes': self.notes,
            'maintenance_interval_days': self.maintenance_interval_days,
            'last_maintenance_at': self.last_maintenance_at.isoformat() if self.last_maintenance_at else None,
            'next_maintenance_at': next_due.isoformat() if next_due else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class Order(db.Model):
    """Etsy orders"""
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    printer_id = db.Column(db.Integer, db.ForeignKey('printers.id'))
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

    # Order management enhancements
    internal_notes = db.Column(db.Text)
    photo_url = db.Column(db.String(512))
    shipping_label_url = db.Column(db.String(512))
    shipping_label_status = db.Column(db.String(50))  # CREATED, PURCHASED, FAILED
    shipping_provider = db.Column(db.String(100))  # shipstation, pirateship, other
    tracking_number = db.Column(db.String(100))
    last_customer_contact_at = db.Column(db.DateTime)
    
    # Production tracking
    production_status = db.Column(db.String(50), default='QUEUED')  # QUEUED, PRINTING, PRINTED, SHIPPED, FAILED
    priority = db.Column(db.Integer, default=3)  # 1=URGENT, 2=HIGH, 3=MEDIUM, 4=LOW, 5=BACKLOG
    print_session_id = db.Column(db.Integer, db.ForeignKey('print_sessions.id'))
    estimated_print_time = db.Column(db.Integer)  # minutes
    actual_print_time = db.Column(db.Integer)  # minutes
    print_started_at = db.Column(db.DateTime)
    print_completed_at = db.Column(db.DateTime)
    print_failures_count = db.Column(db.Integer, default=0)
    print_notes = db.Column(db.Text)
    
    # Relationships
    items = db.relationship('OrderItem', backref='order', lazy=True, cascade='all, delete-orphan')
    filament_usage = db.relationship('FilamentUsage', backref='order', lazy=True)
    
    # Local tracking
    synced_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'customer_id': self.customer_id,
            'printer_id': self.printer_id,
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
            'internal_notes': self.internal_notes,
            'photo_url': self.photo_url,
            'shipping_label_url': self.shipping_label_url,
            'shipping_label_status': self.shipping_label_status,
            'shipping_provider': self.shipping_provider,
            'tracking_number': self.tracking_number,
            'last_customer_contact_at': self.last_customer_contact_at.isoformat() if self.last_customer_contact_at else None,
            'production_status': self.production_status,
            'priority': self.priority,
            'print_session_id': self.print_session_id,
            'estimated_print_time': self.estimated_print_time,
            'actual_print_time': self.actual_print_time,
            'print_started_at': self.print_started_at.isoformat() if self.print_started_at else None,
            'print_completed_at': self.print_completed_at.isoformat() if self.print_completed_at else None,
            'print_failures_count': self.print_failures_count,
            'print_notes': self.print_notes,
            'items': [item.to_dict() for item in self.items],
            'synced_at': self.synced_at.isoformat()
        }


class OrderNote(db.Model):
    """Internal notes/comments per order"""
    __tablename__ = 'order_notes'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'user_id': self.user_id,
            'content': self.content,
            'created_at': self.created_at.isoformat()
        }


class CommunicationLog(db.Model):
    """Customer communication log entries"""
    __tablename__ = 'communication_logs'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    direction = db.Column(db.String(20), default='outbound')  # outbound | inbound
    channel = db.Column(db.String(50), default='message')      # email, message, phone, other
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'user_id': self.user_id,
            'direction': self.direction,
            'channel': self.channel,
            'message': self.message,
            'created_at': self.created_at.isoformat()
        }


class CustomerRequest(db.Model):
    """Track custom product requests from customers"""
    __tablename__ = 'customer_requests'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(50), default='open')  # open, in_progress, delivered, canceled
    priority = db.Column(db.String(20), default='normal')  # low, normal, high
    desired_by = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'customer_id': self.customer_id,
            'title': self.title,
            'description': self.description,
            'status': self.status,
            'priority': self.priority,
            'desired_by': self.desired_by.isoformat() if self.desired_by else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class CustomerFeedback(db.Model):
    """Customer reviews/feedback"""
    __tablename__ = 'customer_feedback'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'))
    rating = db.Column(db.Integer)
    comment = db.Column(db.Text)
    source = db.Column(db.String(50), default='manual')  # etsy, manual
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'customer_id': self.customer_id,
            'order_id': self.order_id,
            'rating': self.rating,
            'comment': self.comment,
            'source': self.source,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Expense(db.Model):
    """Track expenses for materials, shipping supplies, equipment, etc."""
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    category = db.Column(db.String(100), default='other')  # filament, shipping, equipment, overhead, other
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), default='USD')
    description = db.Column(db.Text)
    expense_date = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'category': self.category,
            'amount': self.amount,
            'currency': self.currency,
            'description': self.description,
            'expense_date': self.expense_date.isoformat() if self.expense_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class CustomerFile(db.Model):
    """Store customer-uploaded files (STL, OBJ, images, etc.)"""
    __tablename__ = 'customer_files'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'))
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255))
    file_path = db.Column(db.String(512), nullable=False)
    file_type = db.Column(db.String(50))  # stl, obj, gcode, image, pdf
    file_size = db.Column(db.Integer)  # bytes
    mime_type = db.Column(db.String(100))
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'customer_id': self.customer_id,
            'order_id': self.order_id,
            'filename': self.filename,
            'original_filename': self.original_filename,
            'file_path': self.file_path,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class PrinterConnection(db.Model):
    """OctoPrint/Klipper API connection details"""
    __tablename__ = 'printer_connections'

    id = db.Column(db.Integer, primary_key=True)
    printer_id = db.Column(db.Integer, db.ForeignKey('printers.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    connection_type = db.Column(db.String(50), default='octoprint')  # octoprint, klipper, moonraker, bambu_lan, bambu_cloud
    api_url = db.Column(db.String(512), nullable=False)
    api_key = db.Column(db.String(255))
    serial_number = db.Column(db.String(100))  # For Bambu Lab printers
    access_code = db.Column(db.String(100))  # For Bambu Lab LAN mode
    webhook_enabled = db.Column(db.Boolean, default=False)
    last_connected_at = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='disconnected')  # connected, disconnected, error
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    printer = db.relationship('Printer', backref='connection', uselist=False)

    def to_dict(self):
        return {
            'id': self.id,
            'printer_id': self.printer_id,
            'user_id': self.user_id,
            'connection_type': self.connection_type,
            'api_url': self.api_url,
            'serial_number': self.serial_number,
            'webhook_enabled': self.webhook_enabled,
            'last_connected_at': self.last_connected_at.isoformat() if self.last_connected_at else None,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
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

class PrintSession(db.Model):
    """Batch print sessions for grouping orders"""
    __tablename__ = 'print_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), default='PLANNED')  # PLANNED, IN_PROGRESS, COMPLETED, PAUSED
    total_estimated_time = db.Column(db.Integer, default=0)  # minutes
    total_actual_time = db.Column(db.Integer, default=0)  # minutes
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    orders = db.relationship('Order', backref='print_session', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'status': self.status,
            'total_estimated_time': self.total_estimated_time,
            'total_actual_time': self.total_actual_time,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'notes': self.notes,
            'order_count': len(self.orders),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

class ProductProfile(db.Model):
    """Product templates with standard filament usage"""
    __tablename__ = 'product_profiles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    product_name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    standard_filament_amount = db.Column(db.Float, nullable=False)  # grams per unit
    preferred_material = db.Column(db.String(255))  # PLA, ABS, PETG, etc.
    preferred_color = db.Column(db.String(255))
    print_time_minutes = db.Column(db.Integer)  # Estimated print time
    notes = db.Column(db.Text)
    category = db.Column(db.String(255))
    nozzle_temp_c = db.Column(db.Integer)
    bed_temp_c = db.Column(db.Integer)
    print_speed_mms = db.Column(db.Integer)
    support_settings = db.Column(db.String(255))
    infill_percent = db.Column(db.Float)
    layer_height_mm = db.Column(db.Float)
    material_cost = db.Column(db.Float)
    labor_minutes = db.Column(db.Integer)
    overhead_cost = db.Column(db.Float)
    target_margin_pct = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'product_name': self.product_name,
            'description': self.description,
            'standard_filament_amount': self.standard_filament_amount,
            'preferred_material': self.preferred_material,
            'preferred_color': self.preferred_color,
            'print_time_minutes': self.print_time_minutes,
            'notes': self.notes,
            'category': self.category,
            'nozzle_temp_c': self.nozzle_temp_c,
            'bed_temp_c': self.bed_temp_c,
            'print_speed_mms': self.print_speed_mms,
            'support_settings': self.support_settings,
            'infill_percent': self.infill_percent,
            'layer_height_mm': self.layer_height_mm,
            'material_cost': self.material_cost,
            'labor_minutes': self.labor_minutes,
            'overhead_cost': self.overhead_cost,
            'target_margin_pct': self.target_margin_pct,
            'suggested_price': self._suggested_price(),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

    def _suggested_price(self):
        unit_cost = (self.material_cost or 0) + (self.overhead_cost or 0)
        if self.target_margin_pct:
            return round(unit_cost * (1 + self.target_margin_pct / 100), 2)
        return round(unit_cost, 2) if unit_cost else None


class BambuMaterial(db.Model):
    """Bambu Lab materials loaded on printers"""
    __tablename__ = 'bambu_materials'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    printer_id = db.Column(db.Integer, db.ForeignKey('printers.id'), nullable=False)
    slot = db.Column(db.Integer, nullable=False)  # AMS slot 0-7
    material_type = db.Column(db.String(50))  # PLA, ABS, PETG, TPU, etc.
    color = db.Column(db.String(100))
    weight_grams = db.Column(db.Float)
    remaining_pct = db.Column(db.Float, default=100)  # Percentage remaining
    vendor = db.Column(db.String(255))
    cost_per_kg = db.Column(db.Float)
    loaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_synced = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'printer_id': self.printer_id,
            'slot': self.slot,
            'material_type': self.material_type,
            'color': self.color,
            'weight_grams': self.weight_grams,
            'remaining_pct': self.remaining_pct,
            'remaining_grams': round(self.weight_grams * self.remaining_pct / 100, 1) if self.weight_grams else None,
            'vendor': self.vendor,
            'cost_per_kg': self.cost_per_kg,
            'loaded_at': self.loaded_at.isoformat(),
            'last_synced': self.last_synced.isoformat() if self.last_synced else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class PrintNotification(db.Model):
    """Push notification preferences and history"""
    __tablename__ = 'print_notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    printer_id = db.Column(db.Integer, db.ForeignKey('printers.id'), nullable=False)
    
    # Notification triggers
    notify_print_start = db.Column(db.Boolean, default=True)
    notify_print_complete = db.Column(db.Boolean, default=True)
    notify_print_failed = db.Column(db.Boolean, default=True)
    notify_material_change = db.Column(db.Boolean, default=False)
    notify_maintenance = db.Column(db.Boolean, default=True)
    
    # Notification methods
    email_enabled = db.Column(db.Boolean, default=True)
    webhook_url = db.Column(db.String(500))  # Custom webhook for alerts
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'printer_id': self.printer_id,
            'notify_print_start': self.notify_print_start,
            'notify_print_complete': self.notify_print_complete,
            'notify_print_failed': self.notify_print_failed,
            'notify_material_change': self.notify_material_change,
            'notify_maintenance': self.notify_maintenance,
            'email_enabled': self.email_enabled,
            'webhook_url': self.webhook_url,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class ScheduledPrint(db.Model):
    """Scheduled print jobs on Bambu printers"""
    __tablename__ = 'scheduled_prints'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    printer_id = db.Column(db.Integer, db.ForeignKey('printers.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'))
    
    job_name = db.Column(db.String(255), nullable=False)
    file_name = db.Column(db.String(255))  # 3D model file to print
    
    status = db.Column(db.String(50), default='queued')  # queued, scheduled, started, completed, failed, cancelled
    scheduled_start = db.Column(db.DateTime)  # When to start the print
    estimated_duration_minutes = db.Column(db.Integer)
    
    material_type = db.Column(db.String(50))
    material_slot = db.Column(db.Integer)
    
    # Print parameters from ProductProfile if linked
    nozzle_temp = db.Column(db.Integer)
    bed_temp = db.Column(db.Integer)
    print_speed = db.Column(db.Integer)
    
    # Actual execution
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    failed_reason = db.Column(db.String(500))
    
    # Metadata
    priority = db.Column(db.Integer, default=0)  # Higher = print sooner
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'printer_id': self.printer_id,
            'order_id': self.order_id,
            'job_name': self.job_name,
            'file_name': self.file_name,
            'status': self.status,
            'scheduled_start': self.scheduled_start.isoformat() if self.scheduled_start else None,
            'estimated_duration_minutes': self.estimated_duration_minutes,
            'material_type': self.material_type,
            'material_slot': self.material_slot,
            'nozzle_temp': self.nozzle_temp,
            'bed_temp': self.bed_temp,
            'print_speed': self.print_speed,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'failed_reason': self.failed_reason,
            'priority': self.priority,
            'notes': self.notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class AlertSettings(db.Model):
    """Global alert destinations per user (Slack/Discord/email)."""
    __tablename__ = 'alert_settings'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    slack_webhook_url = db.Column(db.String(500))
    discord_webhook_url = db.Column(db.String(500))
    email_enabled = db.Column(db.Boolean, default=False)
    email_to = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'slack_webhook_url': self.slack_webhook_url,
            'discord_webhook_url': self.discord_webhook_url,
            'email_enabled': self.email_enabled,
            'email_to': self.email_to,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
