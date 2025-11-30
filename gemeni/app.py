# app.py (обновлен для админ-панели и бана)
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
    conn.row_factory = sqlite3.Row # Позволяет обращаться к столбцам по имени
    return conn

def init_db():
    """Инициализирует базу данных, создавая таблицы 'users' и 'messages',
       добавляет поля 'role' и 'is_banned'."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица USERS (пользователи)
    # Добавляем поля role и is_banned
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            displayName TEXT NOT NULL,
            bio TEXT,
            avatarBase64 TEXT,
            emailHash TEXT,
            role TEXT DEFAULT 'user',      -- НОВОЕ: Роль пользователя (user или admin)
            is_banned INTEGER DEFAULT 0    -- НОВОЕ: Статус бана (0 - нет, 1 - да)
        )
    """)
    
    # Таблица MESSAGES (сообщения)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            uuid TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL
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
    
    conn.commit()

    # --- Обновление существующих таблиц (если поля role/is_banned не существуют) ---
    # Этот блок нужен для того, чтобы добавить новые колонки, если база данных уже существовала
    try:
        cursor.execute("SELECT role FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
        conn.commit()
    
    # Добавление начальных пользователей
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        initial_users = [
            # Устанавливаем role='admin' для администратора
            ("admin", "pass", "Администратор Vault", "Создатель", "", 'admin'), 
            ("bob", "pass", "Боб Тестер", "Тестировщик", "", 'user'),
            ("user_me", "pass", "Мой Профиль", "Тестовый пользователь", "", 'user'),
        ]
        
        for user_id, password, display_name, bio, avatar, role in initial_users:
            email_hash = hashlib.md5(f"{user_id}@example.com".encode('utf-8')).hexdigest()
            cursor.execute("""
                INSERT INTO users (id, password, displayName, bio, avatarBase64, emailHash, role)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, password, display_name, bio, avatar, email_hash, role))
    else:
         # Убедимся, что admin имеет правильную роль, если он уже был создан без нее
        cursor.execute("UPDATE users SET role = 'admin' WHERE id = 'admin' AND role != 'admin'")
        
    conn.commit()
    conn.close()

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
    data = request.json
    username = data.get('username')
    password = data.get('password')
    displayName = data.get('displayName')

    if not username or not password or not displayName:
        return jsonify({"status": "error", "message": "Заполните все поля"}), 400
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE id = ?", (username,))
        if cursor.fetchone():
            return jsonify({"status": "error", "message": "Пользователь с таким ID уже есть"}), 409

        email_hash = hashlib.md5(username.encode('utf-8')).hexdigest() 
        
        # Новый пользователь регистрируется с ролью 'user' и is_banned=0
        cursor.execute("""
            INSERT INTO users (id, password, displayName, bio, avatarBase64, emailHash, role, is_banned)
            VALUES (?, ?, ?, ?, ?, ?, 'user', 0)
        """, (username, password, displayName, "", "", email_hash))
        
        conn.commit()
        return jsonify({"status": "success", "message": "Регистрация успешна", "user_id": username})
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": f"Ошибка регистрации: {e}"}), 500
    finally:
        conn.close()


@app.route('/api/login', methods=['POST'])
def login():
    """API для входа пользователя."""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND password = ?", (username, password))
    user_row = cursor.fetchone()
    conn.close()
    
    if user_row:
        user = dict(user_row)
        
        # ПРОВЕРКА НА БАН
        if user['is_banned'] == 1:
            return jsonify({"status": "error", "message": "Аккаунт заблокирован администратором"}), 403
            
        return jsonify({"status": "success", "user": {
            "id": user["id"], 
            "displayName": user["displayName"], 
            "avatarBase64": user.get("avatarBase64", ""), 
            "emailHash": user.get("emailHash", ""),
            "role": user.get("role", "user") # НОВОЕ: Передаем роль пользователя
        }})
    else:
        return jsonify({"status": "error", "message": "Неверный ID или пароль"}), 401


@app.route('/api/profile/<user_id>', methods=['GET', 'POST'])
def profile(user_id):
    """API для просмотра и редактирования профиля, включая загрузку аватарки."""
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
        
        try:
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
            
            return jsonify({"status": "success", "profile": updated_user})
        except Exception as e:
            conn.rollback()
            return jsonify({"status": "error", "message": f"Ошибка сохранения: {e}"}), 500
        finally:
            conn.close()
    
    # GET-запрос: возвращаем текущий профиль
    conn.close()
    return jsonify({"status": "success", "profile": user})


# --- 4. АДМИНИСТРАТИВНЫЕ МАРШРУТЫ ---

@app.route('/api/admin/users', methods=['POST'])
def admin_manage_users():
    """API для администрирования пользователей (просмотр, редактирование, бан)."""
    data = request.json
    admin_id = data.get('admin_id') # ID администратора, который делает запрос
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
        cursor.execute("SELECT id, displayName, role, is_banned, bio FROM users WHERE id != ?", (admin_id,))
        users_list = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify({"status": "success", "users": users_list})

    elif action == 'edit':
        target_id = data.get('target_id')
        new_displayName = data.get('displayName')
        new_password = data.get('password')
        new_is_banned = data.get('is_banned') # 0 или 1
        
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
            
        if not update_parts:
            conn.close()
            return jsonify({"status": "success", "message": "Нет данных для обновления"})
            
        update_params.append(target_id)
        
        try:
            query = "UPDATE users SET " + ", ".join(update_parts) + " WHERE id = ?"
            cursor.execute(query, tuple(update_params))
            conn.commit()
            conn.close()
            return jsonify({"status": "success", "message": f"Профиль пользователя {target_id} обновлен."})
        except Exception as e:
            conn.rollback()
            conn.close()
            return jsonify({"status": "error", "message": f"Ошибка обновления: {e}"}), 500

    conn.close()
    return jsonify({"status": "error", "message": "Неизвестное действие"}), 400

# --- 5. МАРШРУТЫ ЧАТА И ПОИСКА (Без изменений) ---

@app.route('/api/search', methods=['POST'])
# ... (остальной код /api/search) ...
def search():
    """API для поиска пользователей."""
    data = request.json
    current_user_id = data.get('current_user_id')
    term = data.get('term', '').lower()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Поиск по id или displayName, исключая текущего пользователя
    search_term_like = f"%{term}%"
    cursor.execute("""
        SELECT id, displayName, avatarBase64, emailHash 
        FROM users 
        WHERE id != ? AND (id LIKE ? OR displayName LIKE ?)
    """, (current_user_id, search_term_like, search_term_like))
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"status": "success", "results": results})


@app.route('/api/messages', methods=['POST'])
# ... (остальной код /api/messages) ...
def handle_messages():
    """API для отправки сообщений и получения истории чата."""
    data = request.json
    action = data.get('action')
    
    if action == 'send':
        sender_id = data.get('sender_id')
        receiver_id = data.get('receiver_id')
        text = data.get('text')
        
        if not sender_id or not receiver_id or not text:
            return jsonify({"status": "error", "message": "Неполные данные"}), 400

        chat_id = get_chat_id(sender_id, receiver_id)
        
        conn = get_db_connection()
        try:
            # 1. Запись сообщения
            message = {
                "uuid": str(uuid.uuid4()),
                "sender_id": sender_id,
                "text": text,
                "timestamp": datetime.now().strftime("%H:%M")
            }
            conn.execute("""
                INSERT INTO messages (uuid, chat_id, sender_id, text, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (message["uuid"], chat_id, sender_id, text, message["timestamp"]))
            
            # 2. Создание связи чата (если не существует)
            conn.execute("""
                INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                VALUES (?, ?, ?)
            """, (sender_id, receiver_id, chat_id))
            conn.execute("""
                INSERT OR REPLACE INTO chat_partners (user_id, partner_id, chat_id)
                VALUES (?, ?, ?)
            """, (receiver_id, sender_id, chat_id))

            conn.commit()
            return jsonify({"status": "success", "message": message})
        except Exception as e:
            conn.rollback()
            return jsonify({"status": "error", "message": f"Ошибка отправки сообщения: {e}"}), 500
        finally:
            conn.close()

    elif action == 'history':
        user_a = data.get('user_a')
        user_b = data.get('user_b')
        
        if not user_a or not user_b:
            return jsonify({"status": "error", "message": "Необходимо два ID"}), 400

        chat_id = get_chat_id(user_a, user_b)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sender_id, text, timestamp 
            FROM messages 
            WHERE chat_id = ? 
            ORDER BY timestamp ASC
        """, (chat_id,))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                "sender": row["sender_id"],
                "text": row["text"],
                "timestamp": row["timestamp"]
            })
            
        conn.close()
        return jsonify({"status": "success", "messages": history})

    elif action == 'chats':
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"status": "error", "message": "Не указан ID пользователя"}), 400
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                u.id, 
                u.displayName, 
                u.avatarBase64, 
                u.emailHash
            FROM chat_partners cp
            JOIN users u ON cp.partner_id = u.id
            WHERE cp.user_id = ?
        """, (user_id,))
        
        chat_partners = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({"status": "success", "chats": chat_partners})


if __name__ == '__main__':
    app.run(debug=True)