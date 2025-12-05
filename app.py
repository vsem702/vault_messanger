# app.py (полная версия с исправлениями: исчезающие подарки, удаление сообщений и все функции)
from flask import Flask, render_template, request, jsonify
from datetime import datetime
import sqlite3
import hashlib
import uuid
import json

app = Flask(__name__)
DB_NAME = 'vault_messenger.db'

# --- 1. ФУНКЦИИ БАЗЫ ДАННЫХ (SQLite) ---

def get_db_connection():
    """Создает и возвращает подключение к базе данных."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Инициализирует базу данных, создавая все необходимые таблицы."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица USERS (пользователи)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            displayName TEXT NOT NULL,
            bio TEXT,
            avatarBase64 TEXT,
            emailHash TEXT,
            role TEXT DEFAULT 'user',
            is_banned INTEGER DEFAULT 0,
            coins INTEGER DEFAULT 15
        )
    """)
    
    # Таблица MESSAGES (сообщения)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            uuid TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            gift_id TEXT DEFAULT NULL
        )
    """)

    # Таблица CHAT_PARTNERS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_partners (
            user_id TEXT NOT NULL,
            partner_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            PRIMARY KEY (user_id, partner_id)
        )
    """)
    
    # Таблица GIFTS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gifts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            image_url TEXT NOT NULL,
            is_rare BOOLEAN DEFAULT FALSE,
            created_by TEXT DEFAULT 'system',
            quantity INTEGER DEFAULT -1,
            is_active BOOLEAN DEFAULT TRUE,
            upgradeable BOOLEAN DEFAULT FALSE
        )
    """)
    
    # Таблица INVENTORY
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_inventory (
            user_id TEXT NOT NULL,
            gift_id TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            displayed_in_profile BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (user_id, gift_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (gift_id) REFERENCES gifts(id)
        )
    """)
    
    conn.commit()

    # Таблица NFT подарков (уникальные токены)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nft_items (
            token_id TEXT PRIMARY KEY,
            base_gift_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            creator_admin_id TEXT NOT NULL,
            original_sender_id TEXT NOT NULL,
            serial_number INTEGER NOT NULL,
            bg_variant INTEGER NOT NULL,
            price INTEGER NOT NULL,
            is_listed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (base_gift_id) REFERENCES gifts(id),
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    """)

    # Таблицы для групп и каналов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- 'group' или 'channel'
            owner_id TEXT NOT NULL,
            avatarBase64 TEXT,
            about TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS room_members (
            room_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member', -- owner/admin/member
            PRIMARY KEY (room_id, user_id),
            FOREIGN KEY (room_id) REFERENCES rooms(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # --- Проверка и добавление недостающих колонок (миграции) ---
    try:
        cursor.execute("SELECT coins FROM users LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку coins в таблицу users...")
        cursor.execute("ALTER TABLE users ADD COLUMN coins INTEGER DEFAULT 15")
    
    try:
        cursor.execute("SELECT gift_id FROM messages LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку gift_id в таблицу messages...")
        cursor.execute("ALTER TABLE messages ADD COLUMN gift_id TEXT DEFAULT NULL")
    
    try:
        cursor.execute("SELECT is_rare FROM gifts LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем новые колонки в таблицу gifts...")
        cursor.execute("ALTER TABLE gifts ADD COLUMN is_rare BOOLEAN DEFAULT FALSE")
        cursor.execute("ALTER TABLE gifts ADD COLUMN created_by TEXT DEFAULT 'system'")
        cursor.execute("ALTER TABLE gifts ADD COLUMN quantity INTEGER DEFAULT -1")
        cursor.execute("ALTER TABLE gifts ADD COLUMN is_active BOOLEAN DEFAULT TRUE")

    # Новое поле в gifts: upgradeable (можно ли юзерам апгрейдить этот подарок в NFT)
    try:
        cursor.execute("SELECT upgradeable FROM gifts LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку upgradeable в таблицу gifts...")
        cursor.execute("ALTER TABLE gifts ADD COLUMN upgradeable BOOLEAN DEFAULT FALSE")

    # last_seen для статусов онлайн
    try:
        cursor.execute("SELECT last_seen FROM users LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку last_seen в таблицу users...")
        cursor.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")

    # is_read для сообщений
    try:
        cursor.execute("SELECT is_read FROM messages LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку is_read в таблицу messages...")
        cursor.execute("ALTER TABLE messages ADD COLUMN is_read INTEGER DEFAULT 0")

    # displayed_in_profile для NFT
    try:
        cursor.execute("SELECT displayed_in_profile FROM nft_items LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку displayed_in_profile в таблицу nft_items...")
        cursor.execute("ALTER TABLE nft_items ADD COLUMN displayed_in_profile INTEGER DEFAULT 0")

    # новые поля в rooms: avatarBase64 и about
    try:
        cursor.execute("SELECT avatarBase64 FROM rooms LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку avatarBase64 в таблицу rooms...")
        cursor.execute("ALTER TABLE rooms ADD COLUMN avatarBase64 TEXT")
    try:
        cursor.execute("SELECT about FROM rooms LIMIT 1")
    except sqlite3.OperationalError:
        print("Добавляем колонку about в таблицу rooms...")
        cursor.execute("ALTER TABLE rooms ADD COLUMN about TEXT")
    
    conn.commit()
    
    # --- Добавление начальных пользователей ---
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        print("Добавляем начальных пользователей...")
        initial_users = [
            ("admin", "pass", "Администратор Vault", "Создатель системы", "", 'admin'), 
            ("bob", "pass", "Боб Тестер", "Тестировщик приложения", "", 'user'),
            ("user_me", "pass", "Мой Профиль", "Тестовый пользователь", "", 'user'),
        ]
        
        for user_id, password, display_name, bio, avatar, role in initial_users:
            email_hash = hashlib.md5(f"{user_id}@example.com".encode('utf-8')).hexdigest()
            cursor.execute("""
                INSERT INTO users (id, password, displayName, bio, avatarBase64, emailHash, role, coins)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, password, display_name, bio, avatar, email_hash, role, 15))
    else:
        # Обновляем существующих пользователей
        cursor.execute("UPDATE users SET coins = 15 WHERE coins IS NULL")
        cursor.execute("UPDATE users SET role = 'admin' WHERE id = 'admin' AND role != 'admin'")
        
    # --- Добавление начальных подарков ---
    cursor.execute("SELECT COUNT(*) FROM gifts")
    if cursor.fetchone()[0] == 0:
        print("Добавляем начальные подарки...")
        initial_gifts = [
            ("gift1", "❤️ Сердце", 5, "❤️", False, "system", -1, True, False),
            ("gift2", "⭐ Звезда", 10, "⭐", False, "system", -1, True, False),
            ("gift3", "🎁 Подарок", 15, "🎁", False, "system", -1, True, False),
            ("gift4", "🏆 Кубок", 20, "🏆", True, "system", -1, True, True),
            ("gift5", "👑 Корона", 25, "👑", True, "system", -1, True, True),
            ("gift6", "🚀 Ракета", 30, "🚀", True, "system", -1, True, True),
            # Базовый подарок от админа, который можно апгрейдить в NFT
            ("admin_gift", "🎖 Подарок от Админа", 0, "🎖", True, "admin", -1, True, True),
        ]
        
        for gift_id, name, price, image_url, is_rare, created_by, quantity, is_active, upgradeable in initial_gifts:
            cursor.execute("""
                INSERT OR REPLACE INTO gifts (id, name, price, image_url, is_rare, created_by, quantity, is_active, upgradeable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (gift_id, name, price, image_url, is_rare, created_by, quantity, is_active, upgradeable))
        
    conn.commit()
    conn.close()
    print("База данных инициализирована успешно!")

# Вызываем инициализацию базы данных при запуске приложения
init_db()

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def get_chat_id(user_a, user_b):
    """Генерирует уникальный ID чата путем сортировки ID пользователей."""
    return hashlib.md5(json.dumps(sorted([user_a, user_b])).encode('utf-8')).hexdigest()

# --- 3. МАРШРУТЫ АУТЕНТИФИКАЦИИ И ПРОФИЛЯ ---

@app.route('/')
def index():
    """Главная страница, загружает HTML-клиент."""
    return render_template('index.html')

@app.route('/api/register', methods=['POST'])
def register():
    """API для регистрации нового пользователя."""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        displayName = data.get('displayName')

        if not username or not password or not displayName:
            return jsonify({"status": "error", "message": "Заполните все поля"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE id = ?", (username,))
        if cursor.fetchone():
            conn.close()
            return jsonify({"status": "error", "message": "Пользователь с таким ID уже есть"}), 409

        email_hash = hashlib.md5(username.encode('utf-8')).hexdigest() 
        
        cursor.execute("""
            INSERT INTO users (id, password, displayName, bio, avatarBase64, emailHash, role, is_banned, coins)
            VALUES (?, ?, ?, ?, ?, ?, 'user', 0, 15)
        """, (username, password, displayName, "", "", email_hash))
        
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Регистрация успешна", "user_id": username})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка регистрации: {e}"}), 500

@app.route('/api/login', methods=['POST'])
def login():
    """API для входа пользователя."""
    try:
        data = request.json
        username = data.get('username')
        password = data.get('password')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ? AND password = ?", (username, password))
        user_row = cursor.fetchone()
        
        if user_row:
            user = dict(user_row)
            
            # ПРОВЕРКА НА БАН
            if user.get('is_banned') == 1:
                conn.close()
                return jsonify({"status": "error", "message": "Аккаунт заблокирован администратором"}), 403
            
            # обновляем last_seen
            now_str = datetime.now().isoformat(timespec='seconds')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now_str, username))
            conn.commit()
            conn.close()
                
            return jsonify({"status": "success", "user": {
                "id": user["id"], 
                "displayName": user["displayName"], 
                "avatarBase64": user.get("avatarBase64", ""), 
                "emailHash": user.get("emailHash", ""),
                "role": user.get("role", "user"),
                "coins": user.get("coins", 15)
            }})
        else:
            return jsonify({"status": "error", "message": "Неверный ID или пароль"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка входа: {e}"}), 500

@app.route('/api/profile/<user_id>', methods=['GET', 'POST'])
def profile(user_id):
    """API для просмотра и редактирования профиля."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        
        if not user_row:
            conn.close()
            return jsonify({"status": "error", "message": "Пользователь не найден"}), 404

        user = dict(user_row)

        if request.method == 'POST':
            data = request.json
            
            display_name = data.get("displayName", user["displayName"])
            bio = data.get("bio", user["bio"])
            avatar_data = data.get("avatarBase64")
            
            update_query = "UPDATE users SET displayName = ?, bio = ?"
            update_params = [display_name, bio]
            
            if avatar_data is not None:
                update_query += ", avatarBase64 = ?"
                update_params.append(avatar_data)
            
            update_query += " WHERE id = ?"
            update_params.append(user_id)
            
            cursor.execute(update_query, tuple(update_params))
            conn.commit()
            
            # Получаем обновленные данные
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            updated_user = dict(cursor.fetchone())
            
            conn.close()
            return jsonify({"status": "success", "profile": updated_user})
        
        # GET-запрос: возвращаем текущий профиль
        conn.close()
        return jsonify({"status": "success", "profile": user})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка работы с профилем: {e}"}), 500

# --- 4. МАРШРУТЫ ДЛЯ ВАЛЮТЫ И ПОДАРКОВ ---

@app.route('/api/gifts', methods=['GET'])
def get_gifts():
    """API для получения списка доступных подарков."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Добавлено условие AND quantity != 0, чтобы скрывать закончившиеся товары
        cursor.execute("SELECT * FROM gifts WHERE is_active = TRUE AND quantity != 0 ORDER BY price")
        gifts = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"status": "success", "gifts": gifts})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка загрузки подарков: {e}"}), 500


@app.route('/api/admin/my_gifts', methods=['POST'])
def admin_my_gifts():
    """Список подарков, созданных конкретным администратором."""
    try:
        data = request.json
        admin_id = data.get('admin_id')

        if not admin_id:
            return jsonify({"status": "error", "message": "Не указан администратор"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Проверяем, что это администратор
        cursor.execute("SELECT role FROM users WHERE id = ?", (admin_id,))
        row = cursor.fetchone()
        if not row or row['role'] != 'admin':
            conn.close()
            return jsonify({"status": "error", "message": "Нет прав"}), 403

        cursor.execute("""
            SELECT * FROM gifts
            WHERE created_by = ?
            ORDER BY created_by DESC, price ASC
        """, (admin_id,))
        gifts = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return jsonify({"status": "success", "gifts": gifts})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка загрузки подарков администратора: {e}"}), 500

@app.route('/api/inventory/<user_id>', methods=['GET'])
def get_inventory(user_id):
    """API для получения инвентаря пользователя."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT ui.*, g.name, g.image_url, g.is_rare, g.price, g.upgradeable 
            FROM user_inventory ui 
            JOIN gifts g ON ui.gift_id = g.id 
            WHERE ui.user_id = ? AND ui.quantity > 0
        """, (user_id,))
        
        inventory = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"status": "success", "inventory": inventory})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка загрузки инвентаря: {e}"}), 500

@app.route('/api/sell_gift', methods=['POST'])
def sell_gift():
    """API для продажи подарка из инвентаря."""
    try:
        data = request.json
        user_id = data.get('user_id')
        gift_id = data.get('gift_id')
        quantity = data.get('quantity', 1)
        
        if not user_id or not gift_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем наличие подарка в инвентаре
        cursor.execute("SELECT quantity FROM user_inventory WHERE user_id = ? AND gift_id = ?", (user_id, gift_id))
        inventory_item = cursor.fetchone()
        
        if not inventory_item or inventory_item['quantity'] < quantity:
            conn.close()
            return jsonify({"status": "error", "message": "Недостаточно подарков для продажи"}), 400
        
        # Получаем информацию о подарке
        cursor.execute("SELECT price, is_rare FROM gifts WHERE id = ?", (gift_id,))
        gift = cursor.fetchone()
        if not gift:
            conn.close()
            return jsonify({"status": "error", "message": "Подарок не найден"}), 404
        
        gift_price = gift['price']
        # Редкие подарки продаются дороже (80% от стоимости)
        sell_price = int(gift_price * 0.8) if gift['is_rare'] else int(gift_price * 0.5)
        total_sell_price = sell_price * quantity
        
        # Уменьшаем количество в инвентаре
        cursor.execute("""
            UPDATE user_inventory 
            SET quantity = quantity - ? 
            WHERE user_id = ? AND gift_id = ?
        """, (quantity, user_id, gift_id))
        
        # Удаляем запись если количество стало 0
        cursor.execute("DELETE FROM user_inventory WHERE user_id = ? AND gift_id = ? AND quantity <= 0", (user_id, gift_id))
        
        # Начисляем монеты
        cursor.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (total_sell_price, user_id))
        
        conn.commit()
        
        # Получаем новый баланс
        cursor.execute("SELECT coins FROM users WHERE id = ?", (user_id,))
        new_balance = cursor.fetchone()[0]
        
        conn.close()
        return jsonify({
            "status": "success", 
            "message": f"Подарки проданы за {total_sell_price} монет",
            "new_balance": new_balance,
            "sold_quantity": quantity
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка продажи: {e}"}), 500

@app.route('/api/toggle_profile_display', methods=['POST'])
def toggle_profile_display():
    """API для добавления/удаления подарка из профиля."""
    try:
        data = request.json
        user_id = data.get('user_id')
        gift_id = data.get('gift_id')
        
        if not user_id or not gift_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем наличие подарка в инвентаре
        cursor.execute("SELECT displayed_in_profile FROM user_inventory WHERE user_id = ? AND gift_id = ?", (user_id, gift_id))
        inventory_item = cursor.fetchone()
        
        if not inventory_item:
            conn.close()
            return jsonify({"status": "error", "message": "Подарок не найден в инвентаре"}), 404
        
        new_display_state = not inventory_item['displayed_in_profile']
        
        cursor.execute("""
            UPDATE user_inventory 
            SET displayed_in_profile = ? 
            WHERE user_id = ? AND gift_id = ?
        """, (new_display_state, user_id, gift_id))
        
        conn.commit()
        conn.close()
        
        action = "добавлен в" if new_display_state else "удален из"
        return jsonify({
            "status": "success", 
            "message": f"Подарок {action} профиля",
            "displayed_in_profile": new_display_state
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка: {e}"}), 500


@app.route('/api/toggle_nft_profile_display', methods=['POST'])
def toggle_nft_profile_display():
    """Добавление/удаление NFT подарка из профиля пользователя."""
    try:
        data = request.json
        user_id = data.get('user_id')
        token_id = data.get('token_id')

        if not user_id or not token_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT owner_id, displayed_in_profile FROM nft_items WHERE token_id = ?", (token_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "NFT не найден"}), 404
        if row["owner_id"] != user_id:
            conn.close()
            return jsonify({"status": "error", "message": "Вы не владелец этого NFT"}), 403

        new_state = 0 if row["displayed_in_profile"] else 1
        cursor.execute("UPDATE nft_items SET displayed_in_profile = ? WHERE token_id = ?", (new_state, token_id))
        conn.commit()
        conn.close()

        return jsonify({
            "status": "success",
            "message": "NFT " + ("добавлен в профиль" if new_state else "убран из профиля"),
            "displayed_in_profile": bool(new_state)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка: {e}"}), 500

@app.route('/api/send_gift', methods=['POST'])
def send_gift():
    """API для отправки подарка пользователю."""
    try:
        data = request.json
        sender_id = data.get('sender_id')
        receiver_id = data.get('receiver_id')
        gift_id = data.get('gift_id')
        
        if not sender_id or not receiver_id or not gift_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем информацию о подарке
        cursor.execute("SELECT * FROM gifts WHERE id = ? AND is_active = TRUE", (gift_id,))
        gift = cursor.fetchone()
        if not gift:
            conn.close()
            return jsonify({"status": "error", "message": "Подарок не найден"}), 404
        
        gift = dict(gift)
        gift_price = gift['price']
        
        # Проверяем, есть ли подарок в наличии (если не -1)
        if gift['quantity'] == 0:
            conn.close()
            return jsonify({"status": "error", "message": "Этот подарок закончился и исчез из продажи"}), 400
            
        # Если количество > 0, значит это лимитированный товар
        is_limited = gift['quantity'] > 0
        
        # Проверяем баланс отправителя
        cursor.execute("SELECT coins FROM users WHERE id = ?", (sender_id,))
        sender_row = cursor.fetchone()
        if not sender_row:
            conn.close()
            return jsonify({"status": "error", "message": "Отправитель не найден"}), 404
            
        sender_coins = sender_row[0] or 0
        
        if sender_coins < gift_price:
            conn.close()
            return jsonify({"status": "error", "message": "Недостаточно монет"}), 400
        
        chat_id = get_chat_id(sender_id, receiver_id)
        
        message_uuid = str(uuid.uuid4())
        # В тексте оставляем только имя подарка, без base64/URL картинки
        message_text = f"Подарок: {gift['name']}"
        timestamp = datetime.now().strftime("%H:%M")
        
        # Добавляем сообщение в чат
        cursor.execute("""
            INSERT INTO messages (uuid, chat_id, sender_id, text, timestamp, gift_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (message_uuid, chat_id, sender_id, message_text, timestamp, gift_id))
        
        # Создаем связь чата
        cursor.execute("""
            INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
            VALUES (?, ?, ?)
        """, (sender_id, receiver_id, chat_id))
        cursor.execute("""
            INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
            VALUES (?, ?, ?)
        """, (receiver_id, sender_id, chat_id))
        
        # Списание монет у отправителя
        cursor.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (gift_price, sender_id))
        
        # Добавляем подарок в инвентарь получателя
        cursor.execute("""
            INSERT OR REPLACE INTO user_inventory (user_id, gift_id, quantity)
            VALUES (?, ?, COALESCE((SELECT quantity FROM user_inventory WHERE user_id = ? AND gift_id = ?), 0) + 1)
        """, (receiver_id, gift_id, receiver_id, gift_id))
        
        # Уменьшаем количество в магазине (Если это лимитированный подарок)
        if is_limited:
            cursor.execute("UPDATE gifts SET quantity = quantity - 1 WHERE id = ?", (gift_id,))
        
        conn.commit()
        
        # Получаем новый баланс отправителя
        cursor.execute("SELECT coins FROM users WHERE id = ?", (sender_id,))
        new_balance = cursor.fetchone()[0]
        
        message_data = {
            "uuid": message_uuid,
            "sender_id": sender_id,
            "text": message_text,
            "timestamp": timestamp,
            "gift_id": gift_id
        }
        
        conn.close()
        return jsonify({
            "status": "success", 
            "message": message_data,
            "new_balance": new_balance
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка отправки подарка: {e}"}), 500

@app.route('/api/admin/create_gift', methods=['POST'])
def admin_create_gift():
    """API для создания нового подарка администратором."""
    try:
        data = request.json
        admin_id = data.get('admin_id')
        name = data.get('name')
        price = data.get('price')
        image_url = data.get('image_url')
        is_rare = data.get('is_rare', False)
        quantity = data.get('quantity', -1)
        upgradeable = data.get('upgradeable', False)
        
        if not admin_id or not name or not price or not image_url:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем права администратора
        cursor.execute("SELECT role FROM users WHERE id = ?", (admin_id,))
        admin_row = cursor.fetchone()
        if not admin_row or admin_row['role'] != 'admin':
            conn.close()
            return jsonify({"status": "error", "message": "Доступ запрещен"}), 403
        
        # Создаем уникальный ID для подарка
        gift_id = f"gift_{int(datetime.now().timestamp())}"
        
        cursor.execute("""
            INSERT INTO gifts (id, name, price, image_url, is_rare, created_by, quantity, is_active, upgradeable)
            VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, ?)
        """, (gift_id, name, price, image_url, is_rare, admin_id, quantity, upgradeable))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success", 
            "message": "Подарок успешно создан",
            "gift_id": gift_id
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка создания подарка: {e}"}), 500

@app.route('/api/admin/delete_gift', methods=['POST'])
def admin_delete_gift():
    """API для удаления подарка (скрытия из магазина)."""
    try:
        data = request.json
        admin_id = data.get('admin_id')
        gift_id = data.get('gift_id')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверка прав админа
        cursor.execute("SELECT role FROM users WHERE id = ?", (admin_id,))
        admin = cursor.fetchone()
        if not admin or admin['role'] != 'admin':
            conn.close()
            return jsonify({"status": "error", "message": "Нет прав"}), 403

        # Скрываем подарок
        cursor.execute("UPDATE gifts SET is_active = FALSE WHERE id = ?", (gift_id,))
        conn.commit()
        conn.close()
        
        return jsonify({"status": "success", "message": "Подарок удален из магазина"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/admin/toggle_gift_upgradeable', methods=['POST'])
def admin_toggle_gift_upgradeable():
    """Админ включает/выключает возможность апгрейда подарка в NFT."""
    try:
        data = request.json
        admin_id = data.get('admin_id')
        gift_id = data.get('gift_id')
        enable = data.get('enable')

        if admin_id is None or gift_id is None or enable is None:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT role FROM users WHERE id = ?", (admin_id,))
        admin = cursor.fetchone()
        if not admin or admin['role'] != 'admin':
            conn.close()
            return jsonify({"status": "error", "message": "Нет прав"}), 403

        cursor.execute("UPDATE gifts SET upgradeable = ? WHERE id = ?", (1 if enable else 0, gift_id))
        conn.commit()
        conn.close()

        return jsonify({"status": "success", "message": "Настройка апгрейда обновлена", "upgradeable": bool(enable)})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка: {e}"}), 500

@app.route('/api/user/<user_id>', methods=['GET'])
def get_user(user_id):
    """API для получения информации о пользователе с его инвентарем."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Основная информация о пользователе
        cursor.execute("SELECT id, displayName, bio, avatarBase64, emailHash, coins FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        
        if not user_row:
            conn.close()
            return jsonify({"status": "error", "message": "Пользователь не найден"}), 404
        
        user = dict(user_row)
        
        # Подарки в профиле пользователя (обычные)
        cursor.execute("""
            SELECT g.id, g.name, g.image_url, g.is_rare
            FROM user_inventory ui
            JOIN gifts g ON ui.gift_id = g.id
            WHERE ui.user_id = ? AND ui.displayed_in_profile = TRUE AND ui.quantity > 0
        """, (user_id,))
        profile_gifts = [dict(row) for row in cursor.fetchall()]
        user['profile_gifts'] = profile_gifts

        # NFT подарки, отмеченные для профиля
        cursor.execute("""
            SELECT ni.token_id, g.id, g.name, g.image_url, g.is_rare,
                   ni.serial_number, ni.price, ni.bg_variant
            FROM nft_items ni
            JOIN gifts g ON ni.base_gift_id = g.id
            WHERE ni.owner_id = ? AND ni.displayed_in_profile = 1
        """, (user_id,))
        profile_nft = [dict(row) for row in cursor.fetchall()]
        user['profile_nft_gifts'] = profile_nft
        
        conn.close()
        return jsonify({"status": "success", "user": user})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка загрузки пользователя: {e}"}), 500

# --- 5. АДМИНИСТРАТИВНЫЕ МАРШРУТЫ ---

@app.route('/api/admin/users', methods=['POST'])
def admin_manage_users():
    """API для администрирования пользователей."""
    try:
        data = request.json
        admin_id = data.get('admin_id')
        action = data.get('action')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверка, является ли пользователь администратором
        cursor.execute("SELECT role FROM users WHERE id = ?", (admin_id,))
        admin_row = cursor.fetchone()
        if not admin_row or dict(admin_row).get('role') != 'admin':
            conn.close()
            return jsonify({"status": "error", "message": "Доступ запрещен"}), 403

        if action == 'list':
            # Вернуть список всех пользователей, кроме самого администратора
            cursor.execute("SELECT id, displayName, role, is_banned, bio, coins FROM users WHERE id != ?", (admin_id,))
            users_list = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return jsonify({"status": "success", "users": users_list})

        elif action == 'edit':
            target_id = data.get('target_id')
            new_displayName = data.get('displayName')
            new_password = data.get('password')
            new_is_banned = data.get('is_banned')
            new_coins = data.get('coins')
            
            if not target_id:
                conn.close()
                return jsonify({"status": "error", "message": "Не указан целевой пользователь"}), 400
            
            update_parts = []
            update_params = []
            
            if new_displayName is not None:
                update_parts.append("displayName = ?")
                update_params.append(new_displayName)
            
            if new_password is not None and new_password.strip():
                update_parts.append("password = ?")
                update_params.append(new_password)
                
            if new_is_banned is not None and new_is_banned in [0, 1]:
                update_parts.append("is_banned = ?")
                update_params.append(new_is_banned)
                
            if new_coins is not None and new_coins >= 0:
                update_parts.append("coins = ?")
                update_params.append(new_coins)
                
            if not update_parts:
                conn.close()
                return jsonify({"status": "success", "message": "Нет данных для обновления"})
                
            update_params.append(target_id)
            
            query = "UPDATE users SET " + ", ".join(update_parts) + " WHERE id = ?"
            cursor.execute(query, tuple(update_params))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Профиль пользователя {target_id} обновлен."})

        conn.close()
        return jsonify({"status": "error", "message": "Неизвестное действие"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка администрирования: {e}"}), 500


# --- 6.1. NFT ПОДАРКИ И МАРКЕТ ---

@app.route('/api/nft/upgrade', methods=['POST'])
def nft_upgrade_from_inventory():
    """
    Юзер сам апгрейдит обычный подарок из инвентаря в NFT.
    Требует, чтобы подарок был помечен как upgradeable.
    """
    try:
        data = request.json
        user_id = data.get('user_id')
        gift_id = data.get('gift_id')
        price = data.get('price')

        if not user_id or not gift_id or not price:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        price = int(price)
        if price <= 0:
            return jsonify({"status": "error", "message": "Некорректная цена"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Проверяем подарок и то, что он апгрейдится
        cursor.execute("SELECT id, upgradeable FROM gifts WHERE id = ?", (gift_id,))
        gift_row = cursor.fetchone()
        if not gift_row:
            conn.close()
            return jsonify({"status": "error", "message": "Подарок не найден"}), 404
        if not gift_row["upgradeable"]:
            conn.close()
            return jsonify({"status": "error", "message": "Этот подарок нельзя апгрейдить в NFT"}), 400

        # Проверяем наличие в инвентаре
        cursor.execute("""
            SELECT quantity FROM user_inventory
            WHERE user_id = ? AND gift_id = ?
        """, (user_id, gift_id))
        inv = cursor.fetchone()
        if not inv or inv["quantity"] <= 0:
            conn.close()
            return jsonify({"status": "error", "message": "У вас нет такого подарка для апгрейда"}), 400

        # Списываем 1 из инвентаря
        cursor.execute("""
            UPDATE user_inventory
            SET quantity = quantity - 1
            WHERE user_id = ? AND gift_id = ?
        """, (user_id, gift_id))
        cursor.execute("""
            DELETE FROM user_inventory
            WHERE user_id = ? AND gift_id = ? AND quantity <= 0
        """, (user_id, gift_id))

        # Определяем порядковый номер и фон
        cursor.execute("""
            SELECT COUNT(*) FROM nft_items WHERE base_gift_id = ?
        """, (gift_id,))
        serial_number = cursor.fetchone()[0] + 1

        import random
        bg_variant = random.randint(1, 5)
        token_id = f"nft_{uuid.uuid4().hex}"
        created_at = datetime.now().isoformat(timespec='seconds')

        cursor.execute("""
            INSERT INTO nft_items (
                token_id, base_gift_id, owner_id, creator_admin_id,
                original_sender_id, serial_number, bg_variant,
                price, is_listed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (
            token_id, gift_id, user_id, user_id,
            user_id, serial_number, bg_variant,
            price, created_at
        ))

        conn.commit()

        cursor.execute("""
            SELECT ni.*, g.name, g.image_url
            FROM nft_items ni
            JOIN gifts g ON ni.base_gift_id = g.id
            WHERE ni.token_id = ?
        """, (token_id,))
        nft_row = dict(cursor.fetchone())

        conn.close()
        return jsonify({"status": "success", "nft": nft_row})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка апгрейда подарка в NFT: {e}"}), 500


@app.route('/api/status/<user_id>', methods=['GET'])
def user_status(user_id):
    """Статус онлайн / last_seen пользователя."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT last_seen FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({"status": "error", "message": "Пользователь не найден"}), 404
        last_seen = row["last_seen"]
        online = False
        if last_seen:
            try:
                dt = datetime.fromisoformat(last_seen)
                diff = datetime.now() - dt
                online = diff.total_seconds() <= 60
            except Exception:
                online = False
        return jsonify({"status": "success", "online": online, "last_seen": last_seen})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка статуса: {e}"}), 500


@app.route('/api/rooms', methods=['POST'])
def rooms_api():
    """
    Простое API для групп и каналов.
    actions:
      - create: {owner_id, name, type}
      - list: {user_id}
      - join: {room_id, user_id}
      - leave: {room_id, user_id}
    """
    try:
        data = request.json
        action = data.get("action")

        conn = get_db_connection()
        cursor = conn.cursor()

        if action == "create":
            owner_id = data.get("owner_id")
            name = data.get("name")
            # поддерживаем только каналы для публичного поиска
            room_type = "channel"
            avatar_base64 = data.get("avatarBase64")
            about = data.get("about", "")
            if not owner_id or not name:
                conn.close()
                return jsonify({"status": "error", "message": "Неполные данные"}), 400

            room_id = f"{room_type}_{uuid.uuid4().hex[:8]}"
            cursor.execute("""
                INSERT INTO rooms (id, name, type, owner_id, avatarBase64, about)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (room_id, name, room_type, owner_id, avatar_base64, about))
            cursor.execute("""
                INSERT INTO room_members (room_id, user_id, role)
                VALUES (?, ?, 'owner')
            """, (room_id, owner_id))
            
            # Добавляем канал в chat_partners для создателя
            channel_chat_id = f"channel_{room_id}"
            cursor.execute("""
                INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                VALUES (?, ?, ?)
            """, (owner_id, room_id, channel_chat_id))
            
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "room": {"id": room_id, "name": name, "type": room_type}})

        elif action == "list":
            user_id = data.get("user_id")
            if not user_id:
                conn.close()
                return jsonify({"status": "error", "message": "Не указан пользователь"}), 400
            cursor.execute("""
                SELECT r.id, r.name, r.type, r.owner_id, r.avatarBase64, r.about, rm.role
                FROM room_members rm
                JOIN rooms r ON rm.room_id = r.id
                WHERE rm.user_id = ?
            """, (user_id,))
            rooms = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return jsonify({"status": "success", "rooms": rooms})

        elif action == "join":
            room_id = data.get("room_id")
            user_id = data.get("user_id")
            if not room_id or not user_id:
                conn.close()
                return jsonify({"status": "error", "message": "Неполные данные"}), 400
            
            # Проверяем, что это канал
            cursor.execute("SELECT type FROM rooms WHERE id = ?", (room_id,))
            room = cursor.fetchone()
            if not room:
                conn.close()
                return jsonify({"status": "error", "message": "Канал не найден"}), 404
            if room["type"] != "channel":
                conn.close()
                return jsonify({"status": "error", "message": "Это не канал"}), 400
            
            cursor.execute("""
                INSERT OR IGNORE INTO room_members (room_id, user_id, role)
                VALUES (?, ?, 'member')
            """, (room_id, user_id))
            
            # Добавляем канал в chat_partners для пользователя
            channel_chat_id = f"channel_{room_id}"
            cursor.execute("""
                INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                VALUES (?, ?, ?)
            """, (user_id, room_id, channel_chat_id))
            
            conn.commit()
            conn.close()
            return jsonify({"status": "success"})

        elif action == "leave":
            room_id = data.get("room_id")
            user_id = data.get("user_id")
            if not room_id or not user_id:
                conn.close()
                return jsonify({"status": "error", "message": "Неполные данные"}), 400
            cursor.execute("""
                DELETE FROM room_members WHERE room_id = ? AND user_id = ?
            """, (room_id, user_id))
            conn.commit()
            conn.close()
            return jsonify({"status": "success"})

        elif action == "update":
            room_id = data.get("room_id")
            owner_id = data.get("owner_id")
            new_name = data.get("name")
            new_about = data.get("about")
            new_avatar = data.get("avatarBase64")

            if not room_id or not owner_id:
                conn.close()
                return jsonify({"status": "error", "message": "Неполные данные"}), 400

            cursor.execute("SELECT owner_id FROM rooms WHERE id = ?", (room_id,))
            room = cursor.fetchone()
            if not room:
                conn.close()
                return jsonify({"status": "error", "message": "Группа не найдена"}), 404
            if room["owner_id"] != owner_id:
                conn.close()
                return jsonify({"status": "error", "message": "Только владелец может изменять группу"}), 403

            fields = []
            params = []
            if new_name is not None:
                fields.append("name = ?")
                params.append(new_name)
            if new_about is not None:
                fields.append("about = ?")
                params.append(new_about)
            if new_avatar is not None:
                fields.append("avatarBase64 = ?")
                params.append(new_avatar)
            if fields:
                params.append(room_id)
                cursor.execute("UPDATE rooms SET " + ", ".join(fields) + " WHERE id = ?", tuple(params))
                conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": "Группа обновлена"})

        conn.close()
        return jsonify({"status": "error", "message": "Неизвестное действие"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка групп: {e}"}), 500


@app.route('/api/room_broadcast', methods=['POST'])
def room_broadcast():
    """
    Отправка одного и того же сообщения всем участникам группы/канала.
    Использует обычные личные чаты (messages / chat_partners) для каждого участника.
    """
    try:
        data = request.json
        sender_id = data.get('sender_id')
        room_id = data.get('room_id')
        text = data.get('text')

        if not sender_id or not room_id or not text:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Проверяем, что комната существует и что это канал
        cursor.execute("SELECT id, name, type FROM rooms WHERE id = ?", (room_id,))
        room = cursor.fetchone()
        if not room:
            conn.close()
            return jsonify({"status": "error", "message": "Группа не найдена"}), 404
        if room["type"] != "channel":
            conn.close()
            return jsonify({"status": "error", "message": "Только каналы поддерживают рассылку"}), 400

        # Получаем всех участников c ролями
        cursor.execute("""
            SELECT user_id, role FROM room_members
            WHERE room_id = ?
        """, (room_id,))
        members_rows = cursor.fetchall()
        members = [row["user_id"] for row in members_rows]

        if sender_id not in members:
            conn.close()
            return jsonify({"status": "error", "message": "Вы не состоите в этой группе"}), 403

        # проверяем роль отправителя: только owner или admin канала могут писать
        sender_role = None
        for row in members_rows:
            if row["user_id"] == sender_id:
                sender_role = row["role"]
                break
        if sender_role not in ("owner", "admin"):
            conn.close()
            return jsonify({"status": "error", "message": "Только владелец или админ канала может писать в канал"}), 403

        # Для каналов используем специальный chat_id вида "channel_{room_id}"
        channel_chat_id = f"channel_{room_id}"
        now_str = datetime.now().strftime("%H:%M")
        msg_uuid = str(uuid.uuid4())
        
        # Сохраняем одно сообщение для канала (все участники видят одно и то же)
        cursor.execute("""
            INSERT INTO messages (uuid, chat_id, sender_id, text, timestamp, is_read)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (msg_uuid, channel_chat_id, sender_id, text, now_str))

        # Обновляем chat_partners для всех участников канала
        for member_id in members:
            # Каждый участник видит канал как партнера с ID = room_id
            cursor.execute("""
                INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                VALUES (?, ?, ?)
            """, (member_id, room_id, channel_chat_id))

        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Сообщение отправлено в канал"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка рассылки по группе: {e}"}), 500

@app.route('/api/admin/upgrade_to_nft', methods=['POST'])
def admin_upgrade_to_nft():
    """
    Админ создает/апгрейдит NFT-подарок для пользователя.
    Из инвентаря пользователя списывается 1 обычный подарок (если он есть),
    и создается уникальный NFT-токен с порядковым номером и ценой.
    """
    try:
        data = request.json
        admin_id = data.get('admin_id')
        owner_id = data.get('owner_id')
        base_gift_id = data.get('gift_id')
        price = data.get('price')

        if not admin_id or not owner_id or not base_gift_id or not price:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        # Проверяем права администратора
        cursor.execute("SELECT role FROM users WHERE id = ?", (admin_id,))
        admin_row = cursor.fetchone()
        if not admin_row or admin_row['role'] != 'admin':
            conn.close()
            return jsonify({"status": "error", "message": "Нет прав"}), 403

        # Проверяем, что базовый подарок существует
        cursor.execute("SELECT id FROM gifts WHERE id = ?", (base_gift_id,))
        base_gift = cursor.fetchone()
        if not base_gift:
            conn.close()
            return jsonify({"status": "error", "message": "Такого подарка не существует"}), 404

        # Пытаемся списать 1 подарок из инвентаря пользователя (если есть)
        cursor.execute("""
            SELECT quantity FROM user_inventory
            WHERE user_id = ? AND gift_id = ?
        """, (owner_id, base_gift_id))
        inv = cursor.fetchone()
        if inv and inv['quantity'] > 0:
            cursor.execute("""
                UPDATE user_inventory
                SET quantity = quantity - 1
                WHERE user_id = ? AND gift_id = ?
            """, (owner_id, base_gift_id))
            cursor.execute("""
                DELETE FROM user_inventory
                WHERE user_id = ? AND gift_id = ? AND quantity <= 0
            """, (owner_id, base_gift_id))

        # Определяем порядковый номер для этого типа подарка
        cursor.execute("""
            SELECT COUNT(*) FROM nft_items WHERE base_gift_id = ?
        """, (base_gift_id,))
        serial_number = cursor.fetchone()[0] + 1

        # Случайный вариант фона 1..5
        import random
        bg_variant = random.randint(1, 5)

        token_id = f"nft_{uuid.uuid4().hex}"
        created_at = datetime.now().isoformat(timespec='seconds')

        cursor.execute("""
            INSERT INTO nft_items (
                token_id, base_gift_id, owner_id, creator_admin_id,
                original_sender_id, serial_number, bg_variant,
                price, is_listed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (
            token_id, base_gift_id, owner_id, admin_id,
            admin_id, serial_number, bg_variant,
            int(price), created_at
        ))

        conn.commit()

        cursor.execute("""
            SELECT ni.*, g.name, g.image_url
            FROM nft_items ni
            JOIN gifts g ON ni.base_gift_id = g.id
            WHERE ni.token_id = ?
        """, (token_id,))
        token_row = dict(cursor.fetchone())

        conn.close()
        return jsonify({"status": "success", "nft": token_row})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка апгрейда в NFT: {e}"}), 500


@app.route('/api/nft/market', methods=['GET'])
def nft_market_list():
    """Список NFT, выставленных на продажу на маркете."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ni.*, g.name, g.image_url
            FROM nft_items ni
            JOIN gifts g ON ni.base_gift_id = g.id
            WHERE ni.is_listed = 1
            ORDER BY ni.created_at DESC
        """)
        items = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"status": "success", "items": items})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка загрузки маркета: {e}"}), 500


@app.route('/api/nft/my/<user_id>', methods=['GET'])
def nft_my_items(user_id):
    """NFT подарки конкретного пользователя."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ni.*, g.name, g.image_url
            FROM nft_items ni
            JOIN gifts g ON ni.base_gift_id = g.id
            WHERE ni.owner_id = ?
            ORDER BY ni.created_at DESC
        """, (user_id,))
        items = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"status": "success", "items": items})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка загрузки NFT пользователя: {e}"}), 500


@app.route('/api/nft/list', methods=['POST'])
def nft_list_item():
    """Выставить или снять NFT с маркета."""
    try:
        data = request.json
        user_id = data.get('user_id')
        token_id = data.get('token_id')
        price = data.get('price')
        is_listed = data.get('is_listed', True)

        if not user_id or not token_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT owner_id FROM nft_items WHERE token_id = ?", (token_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "NFT не найден"}), 404
        if row['owner_id'] != user_id:
            conn.close()
            return jsonify({"status": "error", "message": "Вы не владелец этого NFT"}), 403

        if is_listed:
            if not price or int(price) <= 0:
                conn.close()
                return jsonify({"status": "error", "message": "Некорректная цена"}), 400
            cursor.execute("""
                UPDATE nft_items
                SET is_listed = 1, price = ?
                WHERE token_id = ?
            """, (int(price), token_id))
        else:
            cursor.execute("""
                UPDATE nft_items
                SET is_listed = 0
                WHERE token_id = ?
            """, (token_id,))

        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Статус NFT обновлен"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка выставления NFT: {e}"}), 500


@app.route('/api/nft/buy', methods=['POST'])
def nft_buy_item():
    """Покупка NFT с маркета за монеты."""
    try:
        data = request.json
        buyer_id = data.get('buyer_id')
        token_id = data.get('token_id')

        if not buyer_id or not token_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM nft_items WHERE token_id = ?", (token_id,))
        token = cursor.fetchone()
        if not token:
            conn.close()
            return jsonify({"status": "error", "message": "NFT не найден"}), 404
        token = dict(token)

        if token['is_listed'] != 1:
            conn.close()
            return jsonify({"status": "error", "message": "NFT не выставлен на продажу"}), 400

        if token['owner_id'] == buyer_id:
            conn.close()
            return jsonify({"status": "error", "message": "Вы уже владелец этого NFT"}), 400

        price = int(token['price'])

        # Проверяем баланс покупателя
        cursor.execute("SELECT coins FROM users WHERE id = ?", (buyer_id,))
        buyer = cursor.fetchone()
        if not buyer:
            conn.close()
            return jsonify({"status": "error", "message": "Покупатель не найден"}), 404
        buyer_coins = buyer['coins'] or 0
        if buyer_coins < price:
            conn.close()
            return jsonify({"status": "error", "message": "Недостаточно монет"}), 400

        # Переводим монеты продавцу
        seller_id = token['owner_id']
        cursor.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (price, buyer_id))
        cursor.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (price, seller_id))

        # Меняем владельца NFT
        cursor.execute("""
            UPDATE nft_items
            SET owner_id = ?, is_listed = 0
            WHERE token_id = ?
        """, (buyer_id, token_id))

        conn.commit()

        cursor.execute("SELECT coins FROM users WHERE id = ?", (buyer_id,))
        new_balance = cursor.fetchone()[0]

        conn.close()
        return jsonify({"status": "success", "new_balance": new_balance})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка покупки NFT: {e}"}), 500


@app.route('/api/nft/regift', methods=['POST'])
def nft_regift():
    """
    Передаривание NFT-подарка за 25 звезд (используем 25 монет как стоимость операции).
    """
    try:
        data = request.json
        from_user = data.get('from_user')
        to_user = data.get('to_user')
        token_id = data.get('token_id')

        if not from_user or not to_user or not token_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        COST = 25  # 25 "звезд" = 25 монет

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM nft_items WHERE token_id = ?", (token_id,))
        token = cursor.fetchone()
        if not token:
            conn.close()
            return jsonify({"status": "error", "message": "NFT не найден"}), 404

        if token['owner_id'] != from_user:
            conn.close()
            return jsonify({"status": "error", "message": "Вы не владелец этого NFT"}), 403

        # Проверяем баланс
        cursor.execute("SELECT coins FROM users WHERE id = ?", (from_user,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Отправитель не найден"}), 404
        balance = row['coins'] or 0
        if balance < COST:
            conn.close()
            return jsonify({"status": "error", "message": "Недостаточно звезд (монет) для передаривания"}), 400

        cursor.execute("UPDATE users SET coins = coins - ? WHERE id = ?", (COST, from_user))
        cursor.execute("""
            UPDATE nft_items
            SET owner_id = ?, is_listed = 0
            WHERE token_id = ?
        """, (to_user, token_id))

        conn.commit()

        cursor.execute("SELECT coins FROM users WHERE id = ?", (from_user,))
        new_balance = cursor.fetchone()[0]

        conn.close()
        return jsonify({"status": "success", "new_balance": new_balance})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка передаривания NFT: {e}"}), 500

# --- 6. УДАЛЕНИЕ СООБЩЕНИЙ ---

@app.route('/api/delete_message', methods=['POST'])
def delete_message():
    """API для удаления сообщения."""
    try:
        data = request.json
        user_id = data.get('user_id')
        message_id = data.get('message_id')
        chat_partner_id = data.get('chat_partner_id')
        
        if not user_id or not message_id:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем роль пользователя
        cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return jsonify({"status": "error", "message": "Пользователь не найден"}), 404
        
        user_role = user['role']
        
        # Проверяем существование сообщения
        cursor.execute("SELECT * FROM messages WHERE uuid = ?", (message_id,))
        message = cursor.fetchone()
        
        if not message:
            conn.close()
            return jsonify({"status": "error", "message": "Сообщение не найдено"}), 404
        
        # Проверяем права: либо пользователь - отправитель, либо админ
        if user_role != 'admin' and message['sender_id'] != user_id:
            conn.close()
            return jsonify({"status": "error", "message": "Нет прав для удаления этого сообщения"}), 403
        
        # Если это подарок – полностью запрещаем удаление
        if message['gift_id']:
            conn.close()
            return jsonify({
                "status": "error",
                "message": "Подарочные сообщения нельзя удалить"
            }), 400
        
        # Удаляем сообщение
        cursor.execute("DELETE FROM messages WHERE uuid = ?", (message_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "status": "success", 
            "message": "Сообщение удалено"
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка удаления сообщения: {e}"}), 500

# --- 7. МАРШРУТЫ ЧАТА И ПОИСКА ---

@app.route('/api/search', methods=['POST'])
def search():
    """API для поиска пользователей и каналов."""
    try:
        data = request.json
        current_user_id = data.get('current_user_id')
        term = data.get('term', '').strip().lower()
        
        print(f"Search request: user={current_user_id}, term='{term}'")
        
        if not current_user_id:
            return jsonify({"status": "error", "message": "Не указан текущий пользователь"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        search_term_like = f"%{term}%"

        # Пользователи
        cursor.execute("""
            SELECT id, displayName, avatarBase64, emailHash 
            FROM users 
            WHERE id != ? AND (id LIKE ? OR displayName LIKE ?)
        """, (current_user_id, search_term_like, search_term_like))
        user_results = [dict(row) for row in cursor.fetchall()]
        for u in user_results:
            u["kind"] = "user"

        # Каналы (rooms.type = 'channel')
        cursor.execute("""
            SELECT id, name, avatarBase64, about, owner_id
            FROM rooms
            WHERE type = 'channel' AND (id LIKE ? OR name LIKE ?)
        """, (search_term_like, search_term_like))
        channel_results = []
        for row in cursor.fetchall():
            d = dict(row)
            d["kind"] = "channel"
            channel_results.append(d)

        conn.close()
        
        all_results = user_results + channel_results
        print(f"Search found {len(all_results)} results (users+channels)")
        return jsonify({"status": "success", "results": all_results})
        
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify({"status": "error", "message": f"Ошибка поиска: {e}"}), 500

@app.route('/api/messages', methods=['POST'])
def handle_messages():
    """API для отправки сообщений и получения истории чата."""
    try:
        data = request.json
        action = data.get('action')
        
        if action == 'send':
            sender_id = data.get('sender_id')
            receiver_id = data.get('receiver_id')
            text = data.get('text')
            
            if not sender_id or not receiver_id or not text:
                return jsonify({"status": "error", "message": "Неполные данные"}), 400

            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Проверяем, является ли receiver_id каналом (проверяем в таблице rooms)
            cursor.execute("SELECT id, name, type FROM rooms WHERE id = ?", (receiver_id,))
            room = cursor.fetchone()
            if room and room["type"] == "channel":
                # Это канал - используем room_broadcast логику
                room_id = receiver_id
                
                # Проверяем роль отправителя
                cursor.execute("""
                    SELECT role FROM room_members
                    WHERE room_id = ? AND user_id = ?
                """, (room_id, sender_id))
                member = cursor.fetchone()
                if not member:
                    conn.close()
                    return jsonify({"status": "error", "message": "Вы не подписаны на этот канал"}), 403
                if member["role"] not in ("owner", "admin"):
                    conn.close()
                    return jsonify({"status": "error", "message": "Только владелец или админ канала может писать"}), 403
                
                # Используем логику room_broadcast
                channel_chat_id = f"channel_{room_id}"
                now_str = datetime.now().strftime("%H:%M")
                msg_uuid = str(uuid.uuid4())
                
                cursor.execute("""
                    INSERT INTO messages (uuid, chat_id, sender_id, text, timestamp, is_read)
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (msg_uuid, channel_chat_id, sender_id, text, now_str))
                
                # Обновляем chat_partners для всех участников
                cursor.execute("""
                    SELECT user_id FROM room_members WHERE room_id = ?
                """, (room_id,))
                members = [row["user_id"] for row in cursor.fetchall()]
                for member_id in members:
                    cursor.execute("""
                        INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                        VALUES (?, ?, ?)
                    """, (member_id, receiver_id, channel_chat_id))
                
                conn.commit()
                conn.close()
                return jsonify({"status": "success", "message": {"uuid": msg_uuid, "sender_id": sender_id, "text": text, "timestamp": now_str}})
            else:
                # Обычный чат между пользователями
                chat_id = get_chat_id(sender_id, receiver_id)
                now_str = datetime.now().strftime("%H:%M")
                message = {
                    "uuid": str(uuid.uuid4()),
                    "sender_id": sender_id,
                    "text": text,
                    "timestamp": now_str
                }
                cursor.execute("""
                    INSERT INTO messages (uuid, chat_id, sender_id, text, timestamp, is_read)
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (message["uuid"], chat_id, sender_id, text, now_str))
                
                cursor.execute("""
                    INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                    VALUES (?, ?, ?)
                """, (sender_id, receiver_id, chat_id))
                cursor.execute("""
                    INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                    VALUES (?, ?, ?)
                """, (receiver_id, sender_id, chat_id))

                conn.commit()
                conn.close()
                return jsonify({"status": "success", "message": message})

        elif action == 'history':
            user_a = data.get('user_a')
            user_b = data.get('user_b')
            
            if not user_a or not user_b:
                return jsonify({"status": "error", "message": "Необходимо два ID"}), 400

            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Проверяем, является ли user_b каналом (проверяем в таблице rooms)
            cursor.execute("SELECT id, type FROM rooms WHERE id = ?", (user_b,))
            room = cursor.fetchone()
            if room and room["type"] == "channel":
                # Это канал - используем специальный chat_id
                chat_id = f"channel_{user_b}"
            else:
                # Обычный чат между пользователями
                chat_id = get_chat_id(user_a, user_b)
            
            cursor.execute("""
                SELECT uuid, sender_id, text, timestamp, gift_id, is_read 
                FROM messages 
                WHERE chat_id = ? 
                ORDER BY timestamp ASC
            """, (chat_id,))
            
            history = []
            for row in cursor.fetchall():
                message_data = {
                    "uuid": row["uuid"],
                    "sender": row["sender_id"],
                    "text": row["text"],
                    "timestamp": row["timestamp"],
                    "is_read": bool(row["is_read"])
                }
                if row["gift_id"]:
                    message_data["gift_id"] = row["gift_id"]
                    message_data["is_gift"] = True
                history.append(message_data)
            
            # помечаем все входящие сообщения как прочитанные (только для обычных чатов)
            if not (room and room["type"] == "channel"):
                cursor.execute("""
                    UPDATE messages
                    SET is_read = 1
                    WHERE chat_id = ? AND sender_id = ? AND is_read = 0
                """, (chat_id, user_b))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "messages": history})

        elif action == 'chats':
            user_id = data.get('user_id')
            if not user_id:
                return jsonify({"status": "error", "message": "Не указан ID пользователя"}), 400
                
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Получаем обычные чаты с пользователями
            cursor.execute("""
                SELECT 
                    u.id, 
                    u.displayName, 
                    u.avatarBase64, 
                    u.emailHash,
                    'user' as chat_type
                FROM chat_partners cp
                JOIN users u ON cp.partner_id = u.id
                WHERE cp.user_id = ? AND cp.partner_id NOT LIKE 'channel_%'
            """, (user_id,))
            chat_partners = [dict(row) for row in cursor.fetchall()]
            
            # Получаем каналы, на которые подписан пользователь
            cursor.execute("""
                SELECT 
                    r.id,
                    r.name as displayName,
                    r.avatarBase64,
                    '' as emailHash,
                    'channel' as chat_type,
                    r.owner_id,
                    rm.role
                FROM room_members rm
                JOIN rooms r ON rm.room_id = r.id
                WHERE rm.user_id = ? AND r.type = 'channel'
            """, (user_id,))
            channels = [dict(row) for row in cursor.fetchall()]
            
            # Объединяем результаты
            all_chats = chat_partners + channels
            conn.close()

            return jsonify({"status": "success", "chats": all_chats})

        return jsonify({"status": "error", "message": "Неизвестное действие"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка работы с сообщениями: {e}"}), 500

# --- CALLS API (WebRTC Signaling) ---

# Хранилище сигналов звонков в памяти (в продакшене лучше использовать Redis)
calls_signaling = {}

@app.route('/api/calls', methods=['POST'])
def handle_calls():
    """API для сигналинга WebRTC звонков."""
    try:
        data = request.json
        action = data.get('action')
        
        if action == 'offer':
            # Инициатор звонка отправляет offer
            caller_id = data.get('caller_id')
            callee_id = data.get('callee_id')
            offer = data.get('offer')
            
            if not caller_id or not callee_id or not offer:
                return jsonify({"status": "error", "message": "Неполные данные"}), 400
            
            call_id = f"{caller_id}_{callee_id}"
            calls_signaling[call_id] = {
                'caller_id': caller_id,
                'callee_id': callee_id,
                'offer': offer,
                'answer': None,
                'caller_ice': [],
                'callee_ice': [],
                'status': 'ringing',
                'created_at': datetime.now().isoformat()
            }
            
            return jsonify({"status": "success", "call_id": call_id})
        
        elif action == 'answer':
            # Получатель звонка отправляет answer
            call_id = data.get('call_id')
            answer = data.get('answer')
            
            if not call_id or not answer:
                return jsonify({"status": "error", "message": "Неполные данные"}), 400
            
            if call_id not in calls_signaling:
                return jsonify({"status": "error", "message": "Звонок не найден"}), 404
            
            calls_signaling[call_id]['answer'] = answer
            calls_signaling[call_id]['status'] = 'answered'
            
            return jsonify({"status": "success"})
        
        elif action == 'ice_candidate':
            # Отправка ICE candidate
            call_id = data.get('call_id')
            candidate = data.get('candidate')
            user_id = data.get('user_id')
            
            if not call_id or not candidate or not user_id:
                return jsonify({"status": "error", "message": "Неполные данные"}), 400
            
            if call_id not in calls_signaling:
                return jsonify({"status": "error", "message": "Звонок не найден"}), 404
            
            call_data = calls_signaling[call_id]
            if user_id == call_data['caller_id']:
                call_data['caller_ice'].append(candidate)
            elif user_id == call_data['callee_id']:
                call_data['callee_ice'].append(candidate)
            else:
                return jsonify({"status": "error", "message": "Неверный пользователь"}), 403
            
            return jsonify({"status": "success"})
        
        elif action == 'get_call':
            # Получение данных звонка (для polling)
            call_id = data.get('call_id')
            user_id = data.get('user_id')
            
            if not call_id or not user_id:
                return jsonify({"status": "error", "message": "Неполные данные"}), 400
            
            if call_id not in calls_signaling:
                return jsonify({"status": "error", "message": "Звонок не найден"}), 404
            
            call_data = calls_signaling[call_id]
            
            # Определяем, какие данные нужны пользователю
            response_data = {
                'status': call_data['status'],
                'caller_id': call_data['caller_id'],
                'callee_id': call_data['callee_id']
            }
            
            if user_id == call_data['caller_id']:
                # Инициатор получает answer и ICE от получателя
                if call_data['answer']:
                    response_data['answer'] = call_data['answer']
                response_data['ice_candidates'] = call_data['callee_ice']
            elif user_id == call_data['callee_id']:
                # Получатель получает offer и ICE от инициатора
                response_data['offer'] = call_data['offer']
                response_data['ice_candidates'] = call_data['caller_ice']
            else:
                return jsonify({"status": "error", "message": "Неверный пользователь"}), 403
            
            return jsonify({"status": "success", "call": response_data})
        
        elif action == 'end_call':
            # Завершение звонка
            call_id = data.get('call_id')
            
            if call_id and call_id in calls_signaling:
                calls_signaling[call_id]['status'] = 'ended'
                # Удаляем через 30 секунд
                import threading
                def cleanup():
                    import time
                    time.sleep(30)
                    if call_id in calls_signaling:
                        del calls_signaling[call_id]
                threading.Thread(target=cleanup, daemon=True).start()
            
            return jsonify({"status": "success"})
        
        elif action == 'check_incoming':
            # Проверка входящих звонков
            user_id = data.get('user_id')
            
            if not user_id:
                return jsonify({"status": "error", "message": "Не указан пользователь"}), 400
            
            # Ищем активные звонки для этого пользователя
            incoming_calls = []
            for call_id, call_data in calls_signaling.items():
                if call_data['callee_id'] == user_id and call_data['status'] == 'ringing':
                    incoming_calls.append({
                        'call_id': call_id,
                        'caller_id': call_data['caller_id'],
                        'offer': call_data['offer']
                    })
            
            return jsonify({"status": "success", "calls": incoming_calls})
        
        return jsonify({"status": "error", "message": "Неизвестное действие"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Ошибка звонка: {e}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)