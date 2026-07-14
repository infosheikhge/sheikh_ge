import os
import sys
import cloudinary
import cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import base64
from functools import wraps

# Import db and models
from models import db, User, Category, Product, ProductImage, Order, OrderItem, \
    Message, MessageReply, MessageAttachment, VoiceMessage, ChatMessage, ChatConversation

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sheikh.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Allowed file extensions
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp3', 'wav', 'm4a', 'ogg', 'webm'}

# Render PostgreSQL support
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sheikh.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp'}


# ============================================================================
# CLOUDINARY CONFIGURATION (მნიშვნელოვანია app-ის შექმნის შემდეგ!)
# ============================================================================

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
    secure=True
)


# Ensure upload folders exist (for local development)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'products'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'messages'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'admin_replies'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'chat_voice'), exist_ok=True)
os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'chat_files'), exist_ok=True)

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@app.template_filter('nl2br')
def nl2br_filter(text):
    if not text:
        return text
    return text.replace('\n', '<br>\n')


def format_message_time(dt):
    if not dt:
        return ''
    now = datetime.now()
    diff = now - dt
    if diff.days == 0:
        return dt.strftime('%H:%M')
    elif diff.days == 1:
        return 'გუშინ'
    elif diff.days < 7:
        return dt.strftime('%A')
    else:
        return dt.strftime('%d.%m.%Y')


# ============================================================================
# Main Routes
# ============================================================================

@app.route('/')
def index():
    products = Product.query.limit(8).all()
    categories = Category.query.all()
    unread_messages_count = Message.query.filter_by(status='unread').count()
    return render_template('index.html', products=products, categories=categories,
                           unread_messages_count=unread_messages_count)


@app.route('/shop')
def shop():
    page = request.args.get('page', 1, type=int)
    category_id = request.args.get('category', type=int)
    search = request.args.get('search', '')
    sort = request.args.get('sort', 'newest')
    view = request.args.get('view', 'grid')

    query = Product.query

    if search:
        query = query.filter(Product.name.ilike(f'%{search}%') | Product.description.ilike(f'%{search}%'))

    if category_id:
        query = query.filter(Product.category_id == category_id)

    if sort == 'price_low':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_high':
        query = query.order_by(Product.price.desc())
    else:
        query = query.order_by(Product.created_at.desc())

    pagination = query.paginate(page=page, per_page=12, error_out=False)
    products = pagination.items
    categories = Category.query.all()
    unread_messages_count = Message.query.filter_by(status='unread').count()

    return render_template('shop.html', products=products, categories=categories,
                           pagination=pagination, current_view=view,
                           unread_messages_count=unread_messages_count)


@app.route('/product/<int:product_id>')
def product(product_id):
    product = Product.query.get_or_404(product_id)
    additional_images = ProductImage.query.filter_by(product_id=product_id).order_by(ProductImage.display_order).all()
    unread_messages_count = Message.query.filter_by(status='unread').count()
    return render_template('product.html', product=product, additional_images=additional_images,
                           unread_messages_count=unread_messages_count)


@app.route('/about')
def about():
    unread_messages_count = Message.query.filter_by(status='unread').count()
    return render_template('about.html', unread_messages_count=unread_messages_count)


@app.route('/contact')
def contact_page():
    """მომხმარებლის ჩატის გვერდი"""
    unread_messages_count = Message.query.filter_by(status='unread').count()
    return render_template('contact.html', unread_messages_count=unread_messages_count)


# ============================================================================
# Cloudinary Upload Helper Functions
# ============================================================================

def upload_to_cloudinary(file, folder='products'):
    """ატვირთავს ფაილს Cloudinary-ზე და აბრუნებს URL-ს"""
    try:
        result = cloudinary.uploader.upload(
            file,
            folder=f'sheikh_ge/{folder}',
            upload_preset=os.environ.get("CLOUDINARY_UPLOAD_PRESET", "sheikh_ge")
        )
        return result['secure_url']
    except Exception as e:
        print(f"❌ Cloudinary upload error: {e}")
        return None


def delete_from_cloudinary(public_id):
    """შლის ფაილს Cloudinary-დან"""
    try:
        result = cloudinary.uploader.destroy(public_id)
        return result.get('result') == 'ok'
    except Exception as e:
        print(f"❌ Cloudinary delete error: {e}")
        return False


# ============================================================================
# Product Image Management Routes (UPDATED - Cloudinary)
# ============================================================================

@app.route('/admin/products/<int:product_id>/images')
@admin_required
def manage_product_images(product_id):
    product = Product.query.get_or_404(product_id)
    images = ProductImage.query.filter_by(product_id=product_id).order_by(ProductImage.display_order).all()
    return render_template('admin/product_images.html', product=product, images=images)


@app.route('/admin/products/<int:product_id>/images/add', methods=['POST'])
@admin_required
def add_product_image(product_id):
    product = Product.query.get_or_404(product_id)
    files = request.files.getlist('images')
    max_order = db.session.query(db.func.max(ProductImage.display_order)).filter_by(product_id=product_id).scalar() or 0

    for idx, file in enumerate(files):
        if file and file.filename and allowed_image(file.filename):
            # Upload to Cloudinary
            cloudinary_url = upload_to_cloudinary(file, f'products/product_{product_id}')
            
            if cloudinary_url:
                image = ProductImage(
                    product_id=product_id,
                    image_path=cloudinary_url,  # Cloudinary URL
                    display_order=max_order + idx + 1
                )
                db.session.add(image)
            else:
                # Fallback to local upload
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"product_{product_id}_{timestamp}_{idx}_{filename}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'products', unique_filename)
                file.save(file_path)
                
                image = ProductImage(
                    product_id=product_id,
                    image_path=f'uploads/products/{unique_filename}',
                    display_order=max_order + idx + 1
                )
                db.session.add(image)

    db.session.commit()
    flash(f'{len(files)} სურათი წარმატებით დაემატა!', 'success')
    return redirect(url_for('manage_product_images', product_id=product_id))


@app.route('/admin/products/images/<int:image_id>/delete', methods=['POST'])
@admin_required
def delete_product_image(image_id):
    image = ProductImage.query.get_or_404(image_id)
    product_id = image.product_id
    
    # Check if it's a Cloudinary URL
    if 'cloudinary.com' in image.image_path:
        # Extract public_id from URL
        # Example: https://res.cloudinary.com/cloud_name/image/upload/v1234567/folder/filename.jpg
        try:
            public_id = image.image_path.split('/')[-1].split('.')[0]
            # Remove version and folder
            parts = image.image_path.split('/')
            for i, part in enumerate(parts):
                if part == 'upload':
                    public_id = '/'.join(parts[i+1:]).split('.')[0]
                    break
            delete_from_cloudinary(public_id)
        except Exception as e:
            print(f"Error deleting from Cloudinary: {e}")
    else:
        # Delete local file
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'products', os.path.basename(image.image_path))
        if os.path.exists(file_path):
            os.remove(file_path)
    
    db.session.delete(image)
    db.session.commit()
    
    flash('სურათი წარმატებით წაიშალა!', 'success')
    return redirect(url_for('manage_product_images', product_id=product_id))


@app.route('/admin/products/images/<int:image_id>/set-main', methods=['POST'])
@admin_required
def set_main_image(image_id):
    image = ProductImage.query.get_or_404(image_id)
    product = image.product
    
    old_main = product.image
    product.image = image.image_path
    image.image_path = old_main
    
    db.session.commit()
    flash('მთავარი ფოტო წარმატებით შეიცვალა!', 'success')
    return redirect(url_for('manage_product_images', product_id=product.id))


# ============================================================================
# CHAT API - USER SIDE
# ============================================================================

@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    """მომხმარებლის მიერ შეტყობინების გაგზავნა"""
    try:
        user_name = request.form.get('name', '').strip()
        user_phone = request.form.get('phone', '').strip()
        user_email = request.form.get('email', '').strip()
        message_text = request.form.get('message', '').strip()
        is_voice = request.form.get('is_voice', 'false') == 'true'
        voice_duration = int(request.form.get('voice_duration', 0))

        if not message_text and not is_voice:
            return jsonify({'success': False, 'error': 'Message is required'}), 400

        user = None
        if current_user.is_authenticated:
            user = current_user
        else:
            if user_email:
                user = User.query.filter_by(email=user_email).first()
            if not user and user_phone:
                user = User.query.filter_by(phone=user_phone).first()

            if not user and user_email:
                try:
                    user = User(
                        name=user_name or 'Guest',
                        email=user_email,
                        phone=user_phone or '',
                        password=generate_password_hash('temp_' + datetime.now().strftime('%Y%m%d%H%M%S'))
                    )
                    db.session.add(user)
                    db.session.flush()
                except Exception as e:
                    print(f"Error creating user: {e}")
                    if user_email:
                        user = User.query.filter_by(email=user_email).first()

        if not user:
            return jsonify({'success': False, 'error': 'User not identified'}), 400

        conversation = ChatConversation.query.filter_by(user_id=user.id, is_active=True).first()
        if not conversation:
            admin = User.query.filter_by(is_admin=True).first()
            conversation = ChatConversation(
                user_id=user.id,
                admin_id=admin.id if admin else None
            )
            db.session.add(conversation)
            db.session.flush()

        voice_path = None
        if is_voice:
            voice_data = request.form.get('voice_data')
            if voice_data and voice_data.startswith('data:audio'):
                try:
                    if ',' in voice_data:
                        audio_data = base64.b64decode(voice_data.split(',')[1])
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        audio_filename = f"user_voice_{user.id}_{timestamp}.webm"
                        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'chat_voice', audio_filename)
                        with open(audio_path, 'wb') as f:
                            f.write(audio_data)
                        voice_path = f'uploads/chat_voice/{audio_filename}'
                except Exception as e:
                    print(f"Error saving voice: {e}")

        file_path = None
        file_name = None
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"user_file_{user.id}_{timestamp}_{filename}"
                save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'chat_files', unique_filename)
                file.save(save_path)
                file_path = f'uploads/chat_files/{unique_filename}'
                file_name = filename

        chat_message = ChatMessage(
            user_id=user.id,
            admin_id=conversation.admin_id,
            message=message_text or (f'[ფაილი: {file_name}]' if file_path else ''),
            is_from_user=True,
            is_read=False,
            is_voice=is_voice,
            voice_path=voice_path,
            voice_duration=voice_duration,
            file_path=file_path,
            file_name=file_name
        )
        db.session.add(chat_message)
        db.session.flush()

        display_message = message_text if message_text else (
            '[ხმოვანი შეტყობინება]' if is_voice else ('[ფაილი]' if file_path else ''))
        conversation.last_message = display_message
        conversation.last_message_time = datetime.now()
        conversation.unread_count += 1
        conversation.updated_at = datetime.now()
        db.session.commit()

        return jsonify({
            'success': True,
            'message_id': chat_message.id,
            'user_id': user.id
        })

    except Exception as e:
        print(f"Error sending message: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/chat/messages')
def chat_get_messages():
    try:
        user_id = request.args.get('user_id', type=int)
        if not user_id and current_user.is_authenticated:
            user_id = current_user.id

        if not user_id:
            return jsonify({'messages': [], 'success': True})

        messages = ChatMessage.query.filter_by(user_id=user_id).order_by(ChatMessage.created_at.asc()).all()

        for msg in messages:
            if not msg.is_from_user and not msg.is_read:
                msg.is_read = True
        db.session.commit()

        conversation = ChatConversation.query.filter_by(user_id=user_id, is_active=True).first()
        if conversation:
            conversation.unread_count = 0
            db.session.commit()

        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'message': msg.message,
                'is_from_user': msg.is_from_user,
                'is_voice': msg.is_voice,
                'voice_path': msg.voice_path,
                'voice_duration': msg.voice_duration,
                'file_path': msg.file_path,
                'file_name': msg.file_name,
                'created_at': msg.created_at.isoformat()
            })

        return jsonify({'messages': result, 'success': True})

    except Exception as e:
        print(f"Error getting messages: {e}")
        return jsonify({'messages': [], 'success': False}), 500


@app.route('/api/chat/check-new')
def chat_check_new():
    try:
        user_id = request.args.get('user_id', type=int)
        last_id = request.args.get('last_id', type=int, default=0)

        if not user_id:
            user_id = current_user.id if current_user.is_authenticated else None

        if not user_id:
            return jsonify({'messages': []})

        messages = ChatMessage.query.filter(
            ChatMessage.user_id == user_id,
            ChatMessage.id > last_id,
            ChatMessage.is_from_user == False
        ).order_by(ChatMessage.created_at.asc()).all()

        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'message': msg.message,
                'is_from_user': msg.is_from_user,
                'is_voice': msg.is_voice,
                'voice_path': msg.voice_path,
                'voice_duration': msg.voice_duration,
                'file_path': msg.file_path,
                'file_name': msg.file_name,
                'created_at': msg.created_at.isoformat()
            })

        return jsonify({'messages': result, 'success': True})

    except Exception as e:
        print(f"Error checking new messages: {e}")
        return jsonify({'messages': [], 'success': False}), 500


# ============================================================================
# CHAT API - ADMIN SIDE
# ============================================================================

@app.route('/admin/api/chat/conversations')
@admin_required
def admin_chat_conversations():
    try:
        conversations = ChatConversation.query.filter_by(is_active=True).order_by(
            ChatConversation.updated_at.desc()
        ).all()

        result = []
        for conv in conversations:
            user = User.query.get(conv.user_id)
            if not user:
                continue

            last_msg = ChatMessage.query.filter_by(user_id=conv.user_id).order_by(
                ChatMessage.created_at.desc()
            ).first()

            unread_count = ChatMessage.query.filter_by(
                user_id=conv.user_id,
                is_from_user=True,
                is_read=False
            ).count()

            result.append({
                'id': conv.id,
                'user_id': user.id,
                'user_name': user.name,
                'user_email': user.email,
                'user_phone': user.phone,
                'last_message': last_msg.message if last_msg else '',
                'last_message_time': last_msg.created_at.isoformat() if last_msg else None,
                'unread_count': unread_count,
                'updated_at': conv.updated_at.isoformat()
            })

        return jsonify({'conversations': result, 'success': True})

    except Exception as e:
        print(f"Error getting conversations: {e}")
        return jsonify({'conversations': [], 'success': False}), 500


@app.route('/admin/api/chat/messages/<int:user_id>')
@admin_required
def admin_chat_messages(user_id):
    try:
        messages = ChatMessage.query.filter_by(user_id=user_id).order_by(
            ChatMessage.created_at.asc()
        ).all()

        for msg in messages:
            if msg.is_from_user and not msg.is_read:
                msg.is_read = True
        db.session.commit()

        conversation = ChatConversation.query.filter_by(user_id=user_id, is_active=True).first()
        if conversation:
            conversation.unread_count = 0
            db.session.commit()

        user = User.query.get(user_id)

        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'message': msg.message,
                'is_from_user': msg.is_from_user,
                'is_voice': msg.is_voice,
                'voice_path': msg.voice_path,
                'voice_duration': msg.voice_duration,
                'file_path': msg.file_path,
                'file_name': msg.file_name,
                'created_at': msg.created_at.isoformat()
            })

        return jsonify({
            'messages': result,
            'user_name': user.name if user else '',
            'user_email': user.email if user else '',
            'user_phone': user.phone if user else '',
            'success': True
        })

    except Exception as e:
        print(f"Error getting admin messages: {e}")
        return jsonify({'messages': [], 'success': False}), 500


@app.route('/admin/api/chat/send', methods=['POST'])
@admin_required
def admin_chat_send():
    try:
        user_id = request.form.get('user_id', type=int)
        message_text = request.form.get('message', '').strip()
        is_voice = request.form.get('is_voice', 'false') == 'true'
        voice_duration = int(request.form.get('voice_duration', 0))

        if not user_id:
            return jsonify({'success': False, 'error': 'User ID required'}), 400

        if not message_text and not is_voice:
            return jsonify({'success': False, 'error': 'Message is required'}), 400

        user = User.query.get(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        # Get or create conversation
        conversation = ChatConversation.query.filter_by(user_id=user_id, is_active=True).first()
        if not conversation:
            conversation = ChatConversation(
                user_id=user_id,
                admin_id=current_user.id
            )
            db.session.add(conversation)
            db.session.flush()

        # Voice message
        voice_path = None
        if is_voice:
            voice_data = request.form.get('voice_data')
            if voice_data and voice_data.startswith('data:audio'):
                try:
                    if ',' in voice_data:
                        audio_data = base64.b64decode(voice_data.split(',')[1])
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        audio_filename = f"admin_voice_{user_id}_{timestamp}.webm"
                        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'chat_voice', audio_filename)
                        with open(audio_path, 'wb') as f:
                            f.write(audio_data)
                        voice_path = f'uploads/chat_voice/{audio_filename}'
                except Exception as e:
                    print(f"Error saving voice: {e}")

        # Create message
        chat_message = ChatMessage(
            user_id=user_id,
            admin_id=current_user.id,
            message=message_text,
            is_from_user=False,
            is_read=False,
            is_voice=is_voice,
            voice_path=voice_path,
            voice_duration=voice_duration
        )
        db.session.add(chat_message)
        db.session.flush()

        # Update conversation
        conversation.last_message = message_text if message_text else '[ხმოვანი შეტყობინება]'
        conversation.last_message_time = datetime.now()
        conversation.unread_count += 1
        conversation.updated_at = datetime.now()
        db.session.commit()

        # Return the saved message
        return jsonify({
            'success': True,
            'message_id': chat_message.id,
            'message': {
                'id': chat_message.id,
                'message': chat_message.message,
                'is_from_user': chat_message.is_from_user,
                'is_voice': chat_message.is_voice,
                'voice_path': chat_message.voice_path,
                'voice_duration': chat_message.voice_duration,
                'created_at': chat_message.created_at.isoformat()
            }
        })

    except Exception as e:
        print(f"❌ ERROR: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/api/chat/check-new/<int:user_id>')
@admin_required
def admin_chat_check_new(user_id):
    try:
        last_id = request.args.get('last_id', type=int, default=0)

        messages = ChatMessage.query.filter(
            ChatMessage.user_id == user_id,
            ChatMessage.id > last_id,
            ChatMessage.is_from_user == True
        ).order_by(ChatMessage.created_at.asc()).all()

        result = []
        for msg in messages:
            result.append({
                'id': msg.id,
                'message': msg.message,
                'is_from_user': msg.is_from_user,
                'is_voice': msg.is_voice,
                'voice_path': msg.voice_path,
                'voice_duration': msg.voice_duration,
                'file_path': msg.file_path,
                'file_name': msg.file_name,
                'created_at': msg.created_at.isoformat()
            })

        return jsonify({'messages': result, 'success': True})

    except Exception as e:
        print(f"Error checking new admin messages: {e}")
        return jsonify({'messages': [], 'success': False}), 500


@app.route('/admin/api/chat/mark-read/<int:user_id>', methods=['POST'])
@admin_required
def admin_chat_mark_read(user_id):
    try:
        messages = ChatMessage.query.filter_by(
            user_id=user_id,
            is_from_user=True,
            is_read=False
        ).all()

        for msg in messages:
            msg.is_read = True

        conversation = ChatConversation.query.filter_by(user_id=user_id, is_active=True).first()
        if conversation:
            conversation.unread_count = 0

        db.session.commit()
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error marking messages as read: {e}")
        return jsonify({'success': False}), 500


@app.route('/admin/api/chat/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_chat_delete(user_id):
    try:
        ChatMessage.query.filter_by(user_id=user_id).delete()
        ChatConversation.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error deleting conversation: {e}")
        db.session.rollback()
        return jsonify({'success': False}), 500


@app.route('/admin/api/search-users')
@admin_required
def admin_search_users():
    try:
        search_term = request.args.get('q', '').strip()
        if not search_term or len(search_term) < 2:
            return jsonify([])

        search_pattern = f"%{search_term}%"
        users = User.query.filter(
            (User.name.ilike(search_pattern)) |
            (User.email.ilike(search_pattern)) |
            (User.phone.ilike(search_pattern))
        ).limit(20).all()

        result = []
        for user in users:
            conv = ChatConversation.query.filter_by(user_id=user.id, is_active=True).first()
            result.append({
                'id': user.id,
                'name': user.name,
                'email': user.email or '',
                'phone': user.phone or '',
                'has_conversation': conv is not None,
                'last_message': conv.last_message if conv else None,
                'last_message_time': conv.last_message_time.isoformat() if conv and conv.last_message_time else None
            })

        return jsonify(result)

    except Exception as e:
        print(f"Error searching users: {e}")
        return jsonify([]), 500


# ============================================================================
# Admin Routes
# ============================================================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    total_products = Product.query.count()
    total_categories = Category.query.count()
    total_orders = Order.query.count()
    total_users = User.query.count()
    unread_messages = Message.query.filter_by(status='unread').count()
    chat_unread = ChatMessage.query.filter_by(is_from_user=True, is_read=False).count()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()

    return render_template('admin/dashboard.html',
                           total_products=total_products,
                           total_categories=total_categories,
                           total_orders=total_orders,
                           total_users=total_users,
                           unread_messages=unread_messages,
                           chat_unread=chat_unread,
                           recent_orders=recent_orders,
                           now=datetime.now())


@app.route('/admin/chat')
@admin_required
def admin_chat():
    return render_template('admin/chat.html')


@app.route('/admin/orders')
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders)


@app.route('/admin/orders/delete/<int:order_id>', methods=['POST'])
@admin_required
def delete_order(order_id):
    order = Order.query.get_or_404(order_id)
    for item in order.items:
        db.session.delete(item)
    db.session.delete(order)
    db.session.commit()
    flash('Order deleted successfully', 'success')
    return redirect(url_for('admin_orders'))


# ============================================================================
# PRODUCT ROUTES WITH CLOUDINARY (UPDATED)
# ============================================================================

@app.route('/admin/products')
@admin_required
def admin_products():
    products = Product.query.all()
    categories = Category.query.all()
    return render_template('admin/products.html', products=products, categories=categories)


@app.route('/admin/products/add', methods=['POST'])
@admin_required
def add_product():
    name = request.form.get('name')
    description = request.form.get('description')
    price = float(request.form.get('price'))
    category_id = int(request.form.get('category_id'))

    image = request.files.get('image')
    if image and image.filename and allowed_image(image.filename):
        # Upload to Cloudinary
        cloudinary_url = upload_to_cloudinary(image, 'products/main')
        if cloudinary_url:
            image_path = cloudinary_url
        else:
            # Fallback to local
            filename = secure_filename(image.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_filename = f"{timestamp}_{filename}"
            image.save(os.path.join(app.config['UPLOAD_FOLDER'], 'products', unique_filename))
            image_path = f'uploads/products/{unique_filename}'
    else:
        image_path = 'uploads/default.jpg'

    product = Product(
        name=name,
        description=description,
        price=price,
        category_id=category_id,
        image=image_path
    )
    db.session.add(product)
    db.session.commit()

    # Handle additional images
    additional_images = request.files.getlist('additional_images')
    for idx, img in enumerate(additional_images):
        if img and img.filename and allowed_image(img.filename):
            cloudinary_url = upload_to_cloudinary(img, f'products/product_{product.id}')
            if cloudinary_url:
                product_image = ProductImage(
                    product_id=product.id,
                    image_path=cloudinary_url,
                    display_order=idx
                )
                db.session.add(product_image)
            else:
                # Fallback to local
                filename = secure_filename(img.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"product_{product.id}_{timestamp}_{idx}_{filename}"
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], 'products', unique_filename)
                img.save(img_path)
                product_image = ProductImage(
                    product_id=product.id,
                    image_path=f'uploads/products/{unique_filename}',
                    display_order=idx
                )
                db.session.add(product_image)

    db.session.commit()
    flash('Product added successfully', 'success')
    return redirect(url_for('admin_products'))


@app.route('/admin/products/edit/<int:product_id>', methods=['GET', 'POST'])
@admin_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)

    if request.method == 'POST':
        product.name = request.form.get('name')
        product.description = request.form.get('description')
        product.price = float(request.form.get('price'))
        product.category_id = int(request.form.get('category_id'))

        image = request.files.get('image')
        if image and image.filename and allowed_image(image.filename):
            # Delete old image if it's on Cloudinary
            if product.image and 'cloudinary.com' in product.image:
                try:
                    public_id = product.image.split('/')[-1].split('.')[0]
                    delete_from_cloudinary(public_id)
                except:
                    pass
            elif product.image and product.image != 'uploads/default.jpg':
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], 'products', os.path.basename(product.image))
                if os.path.exists(old_path):
                    os.remove(old_path)

            # Upload new image to Cloudinary
            cloudinary_url = upload_to_cloudinary(image, 'products/main')
            if cloudinary_url:
                product.image = cloudinary_url
            else:
                # Fallback to local
                filename = secure_filename(image.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                unique_filename = f"{timestamp}_{filename}"
                image.save(os.path.join(app.config['UPLOAD_FOLDER'], 'products', unique_filename))
                product.image = f'uploads/products/{unique_filename}'

        db.session.commit()
        flash('Product updated successfully', 'success')
        return redirect(url_for('admin_products'))

    categories = Category.query.all()
    additional_images = ProductImage.query.filter_by(product_id=product_id).order_by(ProductImage.display_order).all()
    return render_template('admin/edit_product.html', product=product, categories=categories,
                           additional_images=additional_images)


@app.route('/admin/products/delete/<int:product_id>', methods=['POST'])
@admin_required
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)

    # Delete main image from Cloudinary if exists
    if product.image and 'cloudinary.com' in product.image:
        try:
            public_id = product.image.split('/')[-1].split('.')[0]
            delete_from_cloudinary(public_id)
        except:
            pass
    elif product.image and product.image != 'uploads/default.jpg':
        main_path = os.path.join(app.config['UPLOAD_FOLDER'], 'products', os.path.basename(product.image))
        if os.path.exists(main_path):
            os.remove(main_path)

    # Delete additional images
    for img in product.additional_images:
        if img.image_path and 'cloudinary.com' in img.image_path:
            try:
                public_id = img.image_path.split('/')[-1].split('.')[0]
                delete_from_cloudinary(public_id)
            except:
                pass
        else:
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], 'products', os.path.basename(img.image_path))
            if os.path.exists(img_path):
                os.remove(img_path)

    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully', 'success')
    return redirect(url_for('admin_products'))


# ============================================================================
# Admin Categories Routes
# ============================================================================

@app.route('/admin/categories')
@admin_required
def admin_categories():
    categories = Category.query.all()
    total_products = sum(len(category.products) for category in categories)
    active_categories = sum(1 for category in categories if len(category.products) > 0)
    return render_template('admin/categories.html',
                           categories=categories,
                           total_products=total_products,
                           active_categories=active_categories)


@app.route('/admin/categories/add', methods=['POST'])
@admin_required
def add_category():
    name = request.form.get('name')
    description = request.form.get('description')
    category = Category(name=name, description=description)
    db.session.add(category)
    db.session.commit()
    flash('Category added successfully', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/edit/<int:category_id>', methods=['POST'])
@admin_required
def edit_category(category_id):
    category = Category.query.get_or_404(category_id)
    category.name = request.form.get('name')
    category.description = request.form.get('description')
    db.session.commit()
    flash('Category updated successfully', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/delete/<int:category_id>', methods=['POST'])
@admin_required
def delete_category(category_id):
    category = Category.query.get_or_404(category_id)
    if category.products:
        flash('Cannot delete category with products', 'error')
        return redirect(url_for('admin_categories'))
    db.session.delete(category)
    db.session.commit()
    flash('Category deleted successfully', 'success')
    return redirect(url_for('admin_categories'))


# ============================================================================
# Admin Old Message Routes
# ============================================================================

@app.route('/admin/messages')
@admin_required
def admin_messages():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', 'all')

    query = Message.query
    if status != 'all':
        query = query.filter_by(status=status)

    messages = query.order_by(Message.created_at.desc()).paginate(page=page, per_page=20)

    unread_count = Message.query.filter_by(status='unread').count()
    urgent_count = Message.query.filter_by(is_urgent=True, status='unread').count()
    total_count = Message.query.count()

    return render_template('admin/messages.html',
                           messages=messages,
                           unread_count=unread_count,
                           urgent_count=urgent_count,
                           total_count=total_count,
                           current_status=status,
                           unread_messages_count=unread_count)


@app.route('/admin/messages/<int:message_id>')
@admin_required
def view_message(message_id):
    message = Message.query.get_or_404(message_id)
    if message.status == 'unread':
        message.status = 'read'
        db.session.commit()
    return render_template('admin/message_detail.html', message=message)


@app.route('/admin/messages/<int:message_id>/reply-text', methods=['POST'])
@admin_required
def reply_message_text(message_id):
    message = Message.query.get_or_404(message_id)
    reply_text = request.form.get('reply')

    if reply_text:
        reply = MessageReply(
            message_id=message.id,
            admin_id=current_user.id,
            reply_text=reply_text,
            reply_type='text'
        )
        db.session.add(reply)
        message.status = 'replied'
        db.session.commit()
        flash('Text reply sent successfully!', 'success')

    return redirect(url_for('view_message', message_id=message_id))


@app.route('/admin/messages/<int:message_id>/reply-voice', methods=['POST'])
@admin_required
def reply_message_voice(message_id):
    message = Message.query.get_or_404(message_id)

    voice_data = request.form.get('voice_reply')
    if voice_data and voice_data.startswith('data:audio'):
        try:
            if ',' in voice_data:
                audio_data = base64.b64decode(voice_data.split(',')[1])
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                audio_filename = f"reply_admin_{timestamp}_{message_id}.webm"
                audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'admin_replies', audio_filename)

                with open(audio_path, 'wb') as f:
                    f.write(audio_data)

                reply = MessageReply(
                    message_id=message.id,
                    admin_id=current_user.id,
                    reply_text='',
                    reply_type='voice',
                    voice_reply_path=f'uploads/admin_replies/{audio_filename}',
                    voice_duration=int(request.form.get('voice_duration', 0))
                )
                db.session.add(reply)
                message.status = 'replied'
                db.session.commit()

                flash('Voice reply sent successfully!', 'success')
        except Exception as e:
            print(f"Error saving voice reply: {e}")
            flash('Error sending voice reply', 'error')

    return redirect(url_for('view_message', message_id=message_id))


@app.route('/admin/messages/<int:message_id>/delete', methods=['POST'])
@admin_required
def delete_message(message_id):
    message = Message.query.get_or_404(message_id)

    for attachment in message.attachments:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'messages',
                                 os.path.basename(attachment.file_path))
        if os.path.exists(file_path):
            os.remove(file_path)

    if message.voice_message:
        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'messages',
                                  os.path.basename(message.voice_message.audio_path))
        if os.path.exists(audio_path):
            os.remove(audio_path)

    db.session.delete(message)
    db.session.commit()

    flash('Message deleted successfully', 'success')
    return redirect(url_for('admin_messages'))


@app.route('/admin/messages/bulk-action', methods=['POST'])
@admin_required
def bulk_message_action():
    message_ids = request.form.getlist('message_ids')
    action = request.form.get('action')

    if message_ids:
        if action == 'mark_read':
            Message.query.filter(Message.id.in_(message_ids)).update(
                {'status': 'read'}, synchronize_session=False
            )
            flash(f'{len(message_ids)} messages marked as read', 'success')
        elif action == 'delete':
            for msg_id in message_ids:
                message = Message.query.get(msg_id)
                if message:
                    for attachment in message.attachments:
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'messages',
                                                 os.path.basename(attachment.file_path))
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    if message.voice_message:
                        audio_path = os.path.join(app.config['UPLOAD_FOLDER'], 'messages',
                                                  os.path.basename(message.voice_message.audio_path))
                        if os.path.exists(audio_path):
                            os.remove(audio_path)
                    db.session.delete(message)
            flash(f'{len(message_ids)} messages deleted', 'success')

        db.session.commit()

    return redirect(url_for('admin_messages'))


# ============================================================================
# Authentication Routes
# ============================================================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return redirect(url_for('register'))

        user = User(
            name=name,
            email=email,
            phone=phone,
            password=generate_password_hash(password)
        )
        db.session.add(user)
        db.session.commit()

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            next_page = request.args.get('next')
            flash('Logged in successfully!', 'success')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Invalid email or password', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))


# ============================================================================
# Cart Routes
# ============================================================================

@app.route('/add-to-cart/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    try:
        quantity = int(request.form.get('quantity', 1))
        product = Product.query.get_or_404(product_id)

        if 'cart' not in session:
            session['cart'] = {}

        cart_id = str(product_id)
        if cart_id in session['cart']:
            session['cart'][cart_id]['quantity'] += quantity
        else:
            session['cart'][cart_id] = {
                'name': product.name,
                'price': float(product.price),
                'quantity': quantity,
                'image': product.image
            }

        session.modified = True

        return jsonify({
            'success': True,
            'message': 'Item added to cart!',
            'cart_count': len(session['cart'])
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        }), 400


@app.route('/cart')
def cart():
    cart_items = []
    total = 0

    if 'cart' in session:
        for product_id, item in session['cart'].items():
            subtotal = item['price'] * item['quantity']
            total += subtotal
            cart_items.append({
                'id': product_id,
                'name': item['name'],
                'price': item['price'],
                'quantity': item['quantity'],
                'image': item['image'],
                'subtotal': subtotal
            })

    return render_template('cart.html', cart_items=cart_items, total=total)


@app.route('/update-cart/<int:product_id>', methods=['POST'])
@login_required
def update_cart(product_id):
    quantity = int(request.form.get('quantity', 1))

    if 'cart' in session and str(product_id) in session['cart']:
        if quantity > 0:
            session['cart'][str(product_id)]['quantity'] = quantity
        else:
            del session['cart'][str(product_id)]
        session.modified = True

    return redirect(url_for('cart'))


@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        address = request.form.get('address')
        city = request.form.get('city', '')

        order = Order(
            user_id=current_user.id,
            name=name,
            phone=phone,
            address=address,
            city=city,
            total=float(request.form.get('total', 0))
        )
        db.session.add(order)
        db.session.flush()

        if 'cart' in session:
            for product_id, item in session['cart'].items():
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=int(product_id),
                    quantity=item['quantity'],
                    price=item['price']
                )
                db.session.add(order_item)

        db.session.commit()
        session.pop('cart', None)

        flash('Your order has been received. We will contact you soon.', 'success')
        return redirect(url_for('order_confirmation', order_id=order.id))

    total = 0
    if 'cart' in session:
        for item in session['cart'].values():
            total += item['price'] * item['quantity']

    return render_template('checkout.html', total=total)


@app.route('/order-confirmation/<int:order_id>')
@login_required
def order_confirmation(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and not current_user.is_admin:
        flash('Access denied', 'error')
        return redirect(url_for('index'))
    return render_template('order_confirmation.html', order=order)


# ============================================================================
# Run App
# ============================================================================

# ✅ Create tables on app startup (BEFORE running the app)
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables created successfully!")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")

    # Create admin user if not exists
    try:
        admin = User.query.filter_by(email='sheikh@gmail.com').first()
        if not admin:
            admin = User(
                name='Admin',
                email='sheikh@gmail.com',
                phone='555778827',
                password=generate_password_hash('sheikh111'),
                is_admin=True
            )
            db.session.add(admin)
            db.session.commit()
            print("✅ Admin user created: sheikh@gmail.com / sheikh111")
        else:
            print("✅ Admin user already exists!")
    except Exception as e:
        print(f"❌ Error creating admin: {e}")

    # Create test categories if none exist
    try:
        if Category.query.count() == 0:
            categories = [
                Category(name='საათები', description='ლაქშერი საათების კოლექცია'),
                Category(name='სამკაულები', description='ექსკლუზიური სამკაულები'),
                Category(name='ტანსაცმელი', description='პრემიუმ ტანსაცმელი'),
                Category(name='აქსესუარები', description='ელეგანტური აქსესუარები')
            ]
            for cat in categories:
                db.session.add(cat)
            db.session.commit()
            print("✅ Test categories created!")
    except Exception as e:
        print(f"❌ Error creating categories: {e}")

    # Create test product if none exist
    try:
        if Product.query.count() == 0:
            test_product = Product(
                name='Luxury Gold Watch',
                description='Handcrafted luxury timepiece with 18k gold',
                price=2999.99,
                category_id=1,
                image='uploads/default.jpg'
            )
            db.session.add(test_product)
            db.session.commit()
            print("✅ Test product created!")
    except Exception as e:
        print(f"❌ Error creating product: {e}")

if __name__ == '__main__':
    print("🚀 Server starting...")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
