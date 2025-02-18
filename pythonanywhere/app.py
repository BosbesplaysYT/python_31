# ------------------------------Imports--------------------------------

from flask import Flask, request, jsonify, g, render_template, make_response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint                        
from flask_bcrypt import Bcrypt                                 
from flask_cors import CORS                                    
from datetime import datetime, timedelta
import secrets
from werkzeug.utils import secure_filename
import os
import json
import uuid
import random
import string
import time
import glob

# ------------------------------Global variables--------------------------------
app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
UPLOAD_FOLDER = 'static/uploads/profile_pictures'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
session_keys = {}
games = {}
BOARD_SIZE = 10

# --------------------------------Models--------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    lasting_key = db.Column(db.String(200), nullable=True)
    profile_picture = db.Column(db.String(200), nullable=True)  # Allow profile picture to be None
    allows_sharing = db.Column(db.Boolean, default=True)
    role = db.Column(db.String(20), nullable=False, default="user")  # Default role is "user"
    database_dump_tag = db.Column(db.Boolean, default=False, nullable=False)
    
    __table_args__ = (
        CheckConstraint(role.in_(["user", "admin"]), name="check_role_valid"),  # Restrict values
    )

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Now optional
    group_id = db.Column(db.String(36), db.ForeignKey('group.id'), nullable=True)  # New field
    title = db.Column(db.String(100), nullable=True)
    note = db.Column(db.Text, nullable=False)
    tag = db.Column(db.String(100), nullable=True)

    group = db.relationship("Group", backref="notes")

class Group(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.String(36), db.ForeignKey('group.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    admin = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", backref="group_memberships")
    group = db.relationship("Group", backref="members")

class Messages(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)

#---------------------------------Helper functions--------------------------------
def generate_session_key(user_id):
    key = secrets.token_hex(32)
    session_keys[key] = {
        "user_id": user_id,
        "expires_at": datetime.now() + timedelta(minutes=120),
        "last_active": datetime.now()
    }
    return key

def validate_session_key():
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False, "Invalid or missing session API key"

    key = auth_header.split("Bearer ")[1]
    if key not in session_keys:
        return False, "Invalid or missing session API key"

    session = session_keys[key]
    now = datetime.now()

    # Check if key is expired
    if session["expires_at"] < now:
        del session_keys[key]
        return False, "Session expired. Please log in again."

    # Update last active time
    session["last_active"] = now
    return True, session

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Decorator for protected routes
def require_session_key(func):
    def wrapper(*args, **kwargs):
        valid, response = validate_session_key()
        if not valid:
            print("unauthorized")
            return jsonify({"error": response}), 401
        g.user_id = response["user_id"]  # Store user ID for the route
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

def delete_profile_pictures(username):
    """ Deletes all profile pictures associated with the given username. """
    profile_pictures_path = os.path.join(UPLOAD_FOLDER)
    user_pictures = glob.glob(os.path.join(profile_pictures_path, f"{username}_*"))

    for picture in user_pictures:
        try:
            os.remove(picture)
        except Exception as e:
            print(f"Failed to delete {picture}: {e}")


def handle_group_membership(user_id):
    """ Handles removal or admin transfer for groups the user is in. """
    memberships = GroupMember.query.filter_by(user_id=user_id).all()

    for membership in memberships:
        if not membership.admin:
            db.session.delete(membership)
        else:
            group_id = membership.group_id
            group_members = GroupMember.query.filter_by(group_id=group_id).all()

            if len(group_members) == 1:
                delete_group_and_notes(group_id)
            else:
                transfer_admin_rights(group_id, user_id)

            db.session.delete(membership)


def transfer_admin_rights(group_id, admin_id):
    """ Transfers admin rights to the next available group member. """
    next_admin = GroupMember.query.filter(GroupMember.group_id == group_id, GroupMember.user_id != admin_id).first()

    if next_admin:
        next_admin.admin = True
    else:
        raise Exception("No other members found to transfer admin rights.")


def delete_group_and_notes(group_id):
    """ Deletes all notes associated with the group and removes the group. """
    Note.query.filter_by(group_id=group_id).delete()
    Group.query.filter_by(id=group_id).delete()


def delete_user_and_data(user):
    """ Deletes all notes and the user itself. """
    Note.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)

def generate_game_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def generate_bot_ships():
    """
    Randomly generates ship placements for the bot.
    Example ships: sizes 4, 3, 3, 2.
    Ships do not overlap.
    """
    ship_sizes = [4, 3, 3, 2]
    ships = []
    occupied = set()  # to track positions already used

    for size in ship_sizes:
        placed = False
        attempts = 0
        while not placed and attempts < 100:
            attempts += 1
            orientation = random.choice(['horizontal', 'vertical'])
            if orientation == 'horizontal':
                x = random.randint(0, BOARD_SIZE - size)
                y = random.randint(0, BOARD_SIZE - 1)
                positions = [[x + i, y] for i in range(size)]
            else:
                x = random.randint(0, BOARD_SIZE - 1)
                y = random.randint(0, BOARD_SIZE - size)
                positions = [[x, y + i] for i in range(size)]
            # Check for conflicts with already occupied positions.
            if any((pos[0], pos[1]) in occupied for pos in positions):
                continue
            for pos in positions:
                occupied.add((pos[0], pos[1]))
            ships.append({
                "positions": positions,
                "sunk": False
            })
            placed = True
        if not placed:
            print(f"Failed to place ship of size {size}")
    return ships

def process_fire(game, player, x, y):
    """
    Processes a fire action for the given player at (x,y).
    Returns a dict with the result (hit/miss, status, turn, winner, and sunk ship if any).
    """
    opponent = "player1" if player == "player2" else "player2"
    opponent_ships = game["players"][opponent]["ships"]

    hit = False
    for ship in opponent_ships:
        if [x, y] in ship["positions"]:
            hit = True
            game["players"][player]["hits"].append([x, y])
            break

    if not hit:
        game["players"][player]["misses"].append([x, y])
        game["players"][opponent]["incoming_misses"].append({"pos": [x, y], "timestamp": time.time()})

    # Check win condition.
    all_opponent_positions = []
    for ship in opponent_ships:
        all_opponent_positions.extend(ship["positions"])
    if all([pos in game["players"][player]["hits"] for pos in all_opponent_positions]):
        game["status"] = "gameover"
        game["winner"] = player

    sunk_ship = None
    if hit:
        for ship in opponent_ships:
            if not ship.get("sunk", False) and all(pos in game["players"][player]["hits"] for pos in ship["positions"]):
                ship["sunk"] = True
                sunk_ship = ship
                break
    else:
        # On a miss, change turn.
        game["turn"] = opponent

    return {
        "hit": hit,
        "status": game["status"],
        "turn": game["turn"],
        "winner": game["winner"],
        "sunk": sunk_ship
    }

def already_fired(bot, x, y):
    """Checks if the bot has already fired at the given (x,y)."""
    return [x, y] in bot.get("hits", []) or [x, y] in bot.get("misses", [])

def bot_move(game_code):
    """
    Called when it is the bot's turn. Uses two modes:
    1. "search": picks a random unfired cell.
    2. "target": after a hit, picks an adjacent cell and, if a second hit occurs,
       deduces the ship’s orientation and continues in that direction.
    If a move is a miss, turn passes to the human.
    """
    game = games[game_code]
    bot = game["players"]["player2"]

    # Initialize bot state if not already set.
    if "botState" not in bot:
        bot["botState"] = {
            "mode": "search",       # can be "search" or "target"
            "target_hits": [],      # list of consecutive hit coordinates
            "potential_targets": [] # list of next coordinates to try
        }
    state = bot["botState"]

    if state["mode"] == "search":
        # Build list of all cells not already fired at.
        possible_moves = []
        for x in range(BOARD_SIZE):
            for y in range(BOARD_SIZE):
                if not already_fired(bot, x, y):
                    possible_moves.append([x, y])
        if not possible_moves:
            return {"error": "No more moves available"}
        move = random.choice(possible_moves)
    else:
        # In "target" mode, if potential targets are not computed, use the first hit.
        if not state["potential_targets"]:
            first_hit = state["target_hits"][0]
            x, y = first_hit
            adjacent = []
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < BOARD_SIZE and 0 <= ny < BOARD_SIZE and not already_fired(bot, nx, ny):
                    adjacent.append([nx, ny])
            state["potential_targets"] = adjacent

        if state["potential_targets"]:
            move = random.choice(state["potential_targets"])
        else:
            # Fallback: reset to search mode if no targets available.
            state["mode"] = "search"
            return bot_move(game_code)

    x, y = move
    result = process_fire(game, "player2", x, y)
    print(f"Bot fires at {(x, y)}: {result}")

    if result["hit"]:
        state["target_hits"].append([x, y])
        if len(state["target_hits"]) == 1:
            state["mode"] = "target"
        elif len(state["target_hits"]) >= 2:
            # Deduce orientation based on the first two hits.
            hit1 = state["target_hits"][0]
            hit2 = state["target_hits"][1]
            potential = []
            if hit1[0] == hit2[0]:
                # Vertical ship: try one cell above and one below.
                col = hit1[0]
                ys = [hit[1] for hit in state["target_hits"]]
                min_y, max_y = min(ys), max(ys)
                if min_y - 1 >= 0 and not already_fired(bot, col, min_y - 1):
                    potential.append([col, min_y - 1])
                if max_y + 1 < BOARD_SIZE and not already_fired(bot, col, max_y + 1):
                    potential.append([col, max_y + 1])
            elif hit1[1] == hit2[1]:
                # Horizontal ship: try one cell left and one cell right.
                row = hit1[1]
                xs = [hit[0] for hit in state["target_hits"]]
                min_x, max_x = min(xs), max(xs)
                if min_x - 1 >= 0 and not already_fired(bot, min_x - 1, row):
                    potential.append([min_x - 1, row])
                if max_x + 1 < BOARD_SIZE and not already_fired(bot, max_x + 1, row):
                    potential.append([max_x + 1, row])
            state["potential_targets"] = potential

        # Reset targeting if a ship was sunk.
        if result.get("sunk"):
            state["mode"] = "search"
            state["target_hits"] = []
            state["potential_targets"] = []

        # If the bot still has the turn, let it fire again.
        if game["status"] == "battle" and game["turn"] == "player2":
            time.sleep(0.5)
            bot_move(game_code)
    else:
        # In target mode, remove this move from potential targets.
        if state["mode"] == "target" and move in state["potential_targets"]:
            state["potential_targets"].remove(move)
    return result

#---------------------------------Template routes--------------------------------

@app.errorhandler(404)
def page_note_found(error):
    return render_template('404.html'), 404

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/login_page')
def login_page():
    return render_template('login.html')

@app.route('/signup_page')
def signup_page():
    return render_template('signup.html')


@app.route('/account_page')
def account_page():
    return render_template('account.html')

@app.route('/admin_page')
def admin_page():
    return render_template('admin.html')

@app.route('/group-notes')
def group_notes():
    return render_template("group_index.html")

@app.route('/setup')
def setup():
    return render_template('setup.html')

@app.route('/pws')
def pws():
    return render_template('pws.html')

@app.route('/battle')
def battle():
    return render_template('battle.html')

@app.route('/spectate_callback')
def spectate():
    return render_template('spectate.html')

@app.route('/spectate')
def spectate_setup():
    return render_template('spectate_list.html')


#---------------------------------API routes--------------------------------

# Homepage

@app.route('/contact', methods=['POST'])
def contact():
    data = request.json
    email = data['email']
    message = data['message']
    try:
        message = Messages(email=email, message=message)
        db.session.add(message)
        db.session.commit()
        return jsonify({"succes": "Message received successfully!"}), 201
    except Exception as e:
        return jsonify({"error": f"Message not received {e}"}), 400
    

# User info

@app.route('/user-info', methods=['GET'])
@require_session_key
def get_user_info():
    user = User.query.get(g.user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "username": user.username,
        "profile_picture": user.profile_picture,
        "allows_sharing": user.allows_sharing,
        "role": user.role
    }), 200

# Groups

@app.route('/check-group')
@require_session_key
def group_info_lol():
    print("Entering group_info route")
    user_id = User.query.get(g.user_id)
    print(f"Queried user_id: {user_id}")

    if not user_id:
        print("User not found")
        return jsonify({"error": "User not found"}), 400
    
    group_membership = GroupMember.query.filter_by(user_id=user_id.id).first()
    print(f"Queried group_membership: {group_membership}")

    if not group_membership:
        print("User is not in any group")
        return jsonify({"error": "User is not in any group"}), 404

    print("User is in a group")
    return jsonify({
        "message": "User is in a group",
        "group_id": group_membership.group_id,
        "is_admin": group_membership.admin
    }), 200

@app.route('/group-info/<group_id>')
@require_session_key
def group_info(group_id):
    # Get the group
    group = Group.query.get(group_id)
    if not group:
        return jsonify({"error": "Group not found"}), 404

    # Get the current user's membership (to check if they are admin)
    current_membership = GroupMember.query.filter_by(group_id=group_id, user_id=g.user_id).first()
    if not current_membership:
        return jsonify({"error": "User is not in this group"}), 403

    # Query all members of the group
    memberships = GroupMember.query.filter_by(group_id=group_id).all()
    members_list = []
    for membership in memberships:
        user = User.query.get(membership.user_id)
        # If the profile picture exists, clean it; otherwise, set it to None.
        if user.profile_picture:
            cleaned_profile_pic = user.profile_picture.replace("\\", "/")
        else:
            cleaned_profile_pic = None

        members_list.append({
            "user_id": user.id,
            "username": user.username,
            "profile_pic": cleaned_profile_pic,
            "is_admin": membership.admin
        })
        print(members_list)

    return jsonify({
        "group_name": group.name,
        "group_members": members_list,
        "current_user_admin": current_membership.admin
    }), 200
    
@app.route('/groups', methods=['POST'])
@require_session_key
def create_group():
    print("Entering create_group route")
    data = request.json
    print(f"Received data: {data}")
    name = data.get('name')
    print(f"Group name: {name}")

    if not name:
        print("Group name is required")
        return jsonify({"error": "Group name is required"}), 400

    new_group = Group(name=name)
    db.session.add(new_group)
    db.session.commit()
    print(f"Created new group: {new_group}")

    # Automatically add the creator as a member
    membership = GroupMember(user_id=g.user_id, group_id=new_group.id, admin=True)
    db.session.add(membership)
    db.session.commit()
    print(f"Added creator as member: {membership}")

    return jsonify({"message": "Group created successfully!", "group_id": new_group.id}), 201

@app.route('/groups/join', methods=['POST'])
@require_session_key
def join_group():
    print("Entering join_group route")
    data = request.json
    print(f"Received data: {data}")
    group_id = data.get('group_id')
    print(f"Group ID: {group_id}")

    group = Group.query.get(group_id)
    print(f"Queried group: {group}")
    if not group:
        print("Group not found")
        return jsonify({"error": "Group not found"}), 404

    # Check if user is already in the group
    existing_member = GroupMember.query.filter_by(user_id=g.user_id, group_id=group_id).first()
    print(f"Queried existing_member: {existing_member}")
    if existing_member:
        print("Already a member of this group")
        return jsonify({"message": "Already a member of this group"}), 200

    # Check if the group has no members
    has_members = GroupMember.query.filter_by(group_id=group_id).first()
    is_admin = not has_members  # If no members, the joining user becomes admin
    print(f"Is first member (admin): {is_admin}")

    membership = GroupMember(user_id=g.user_id, group_id=group_id, admin=is_admin)
    db.session.add(membership)
    db.session.commit()
    print(f"Joined group successfully: {membership}")

    return jsonify({"message": "Joined group successfully!"}), 200

@app.route('/groups/leave', methods=['POST'])
@require_session_key
def leave_group():
    print("Entering leave_group route")
    data = request.json
    print(f"Received data: {data}")
    group_id = data.get('group_id')
    print(f"Group ID: {group_id}")

    group = Group.query.get(group_id)
    print(f"Queried group: {group}")
    if not group:
        print("Group not found")
        return jsonify({"error": "Group not found"}), 404

    # Check if user is already in the group
    existing_member = GroupMember.query.filter_by(user_id=g.user_id, group_id=group_id).first()
    print(f"Queried existing_member: {existing_member}")
    
    if not existing_member:
        print("Not a member of this group")
        return jsonify({"message": "Not a member of this group!"}), 403

    # Delete the existing membership from the database
    db.session.delete(existing_member)
    db.session.commit()  # Commit the transaction

    print("Left group successfully: Membership deleted")
    return jsonify({"message": "Left group successfully!"}), 200

@app.route('/groups/remove-user', methods=['POST'])
@require_session_key
def remove_user_from_group():
    data = request.get_json()
    group_id = data.get("group_id")
    user_id_to_remove = data.get("user_id")

    # Verify that the current user is an admin of the group
    admin_membership = GroupMember.query.filter_by(group_id=group_id, user_id=g.user_id).first()
    if not admin_membership or not admin_membership.admin:
        return jsonify({"error": "Unauthorized: only admins can remove users."}), 403

    # Find the membership for the user to remove
    member_to_remove = GroupMember.query.filter_by(group_id=group_id, user_id=user_id_to_remove).first()
    if not member_to_remove:
        return jsonify({"error": "User not found in the group."}), 404

    db.session.delete(member_to_remove)
    db.session.commit()
    return jsonify({"message": "User removed successfully."}), 200

@app.route('/groups/delete', methods=['POST'])
@require_session_key
def delete_group():
    data = request.get_json()
    group_id = data.get("group_id")

    # Verify that the current user is an admin of the group
    admin_membership = GroupMember.query.filter_by(group_id=group_id, user_id=g.user_id).first()
    if not admin_membership or not admin_membership.admin:
        return jsonify({"error": "Unauthorized: only admins can delete the group."}), 403

    # Remove all group memberships
    GroupMember.query.filter_by(group_id=group_id).delete()
    # Remove all notes belonging to the group
    Note.query.filter_by(group_id=group_id).delete()
    # Remove the group itself
    group = Group.query.get(group_id)
    if group:
        db.session.delete(group)

    db.session.commit()
    return jsonify({"message": "Group deleted successfully."}), 200

# Group notes

@app.route('/groups/<string:group_id>/notes', methods=['GET'])
@require_session_key
def get_group_notes(group_id):
    print("Entering get_group_notes route")
    print(f"Group ID: {group_id}")

    # Ensure the user is part of the group
    membership = GroupMember.query.filter_by(user_id=g.user_id, group_id=group_id).first()
    print(f"Queried membership: {membership}")
    if not membership:
        print("Not a member of this group")
        return jsonify({"error": "Not a member of this group"}), 403

    notes = Note.query.filter_by(group_id=group_id).all()
    print(f"Queried notes: {notes}")
    sanitized_notes = [{"id": note.id, "title": note.title, "note": note.note, "tag": note.tag} for note in notes]
    print(f"Sanitized notes: {sanitized_notes}")

    return jsonify(sanitized_notes)

@app.route('/groups/<string:group_id>/notes', methods=['POST'])
@require_session_key
def add_group_note(group_id):
    print("Entering add_group_note route")
    data = request.json
    print(f"Received data: {data}")
    title = data.get('title')
    note_text = data['note']
    tag = data.get('tag')
    print(f"Title: {title}, Note: {note_text}, Tag: {tag}")

    # Ensure user is in the group
    membership = GroupMember.query.filter_by(user_id=g.user_id, group_id=group_id).first()
    print(f"Queried membership: {membership}")
    if not membership:
        print("Not a member of this group")
        return jsonify({"error": "Not a member of this group"}), 403

    new_note = Note(group_id=group_id, title=title, note=note_text, tag=tag)
    db.session.add(new_note)
    db.session.commit()
    print(f"Added new note: {new_note}")

    return jsonify({"message": "Group note added successfully!"}), 201

@app.route('/groups/<string:group_id>/notes', methods=['PUT', 'DELETE'])
@require_session_key
def update_delete_group_note(group_id):
    print("Entering update_delete_group_note route")
    print(f"Group ID: {group_id}")

    # Use filter_by to query by group_id
    note = Note.query.filter_by(group_id=group_id).first()
    print(f"Queried note: {note}")

    if not note or note.group_id != group_id:
        print("Note not found")
        return jsonify({"error": "Note not found"}), 404

    # Ensure user is part of the group
    membership = GroupMember.query.filter_by(user_id=g.user_id, group_id=group_id).first()
    print(f"Queried membership: {membership}")
    if not membership:
        print("Not a member of this group")
        return jsonify({"error": "Not a member of this group"}), 403

    if request.method == 'PUT':
        data = request.json
        print(f"Received data: {data}")
        note.title = data.get('title')
        note.note = data['note']
        note.tag = data.get('tag')
        db.session.commit()
        print("Note updated successfully")
        return jsonify({"message": "Note updated successfully!"}), 200

    elif request.method == 'DELETE':
        db.session.delete(note)
        db.session.commit()
        print("Note deleted successfully")
        return jsonify({"message": "Note deleted successfully!"}), 200

# Sharing notes

@app.route('/share-note/<int:note_id>', methods=['POST'])
@require_session_key
def share_note(note_id):
    data = request.get_json()
    username = data.get('username')

    if not username:
        return jsonify({"error": "Username is required"}), 400

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    if user.id == g.user_id:
        return jsonify({"error": "You cannot share a note with yourself"}), 400
    
    allows_sharing = user.allows_sharing
    if not allows_sharing:
        return jsonify({"error": "User does not allow sharing notes"}), 400

    original_note = Note.query.get(note_id)
    if not original_note:
        return jsonify({"error": "Note not found"}), 404

    try:
        new_note = Note(
            user_id=user.id,
            title=original_note.title,
            note=original_note.note,
            tag=original_note.tag
        )
        db.session.add(new_note)
        db.session.commit()
        return jsonify({"message": "Note shared successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# Profile

@app.route('/update-password', methods=['PUT'])
@require_session_key
def update_password():
    data = request.json
    user = User.query.get(g.user_id)  # Use g.user_id directly
    if not user:
        return jsonify({"error": "User not found"}), 404

    if bcrypt.check_password_hash(user.password, data['current_password']):
        hashed_password = bcrypt.generate_password_hash(data['new_password']).decode('utf-8')
        user.password = hashed_password
        db.session.commit()
        return jsonify({"message": "Password updated successfully!"}), 200
    else:
        return jsonify({"error": "Current password is incorrect"}), 400

@app.route('/update-username', methods=['PUT'])
@require_session_key
def update_username():
    data = request.json
    user = User.query.get(g.user_id)  # Use g.user_id directly
    if not user:
        return jsonify({"error": "User not found"}), 404

    if User.query.filter_by(username=data['new_username']).first():
        return jsonify({"error": "Username already exists"}), 400

    user.username = data['new_username']
    db.session.commit()
    return jsonify({"message": "Username updated successfully!"}), 200

@app.route('/delete-account', methods=['DELETE'])
@require_session_key
def delete_account():
    data = request.json
    user = User.query.get(g.user_id)
    
    if not user:
        return jsonify({"error": "User not found"}), 404

    if not bcrypt.check_password_hash(user.password, data['password']):
        return jsonify({"error": "Incorrect password"}), 400

    try:
        delete_profile_pictures(user.username)
        handle_group_membership(user.id)
        delete_user_and_data(user)
        
        db.session.commit()
        return jsonify({"message": "Account and all related data deleted successfully!"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    
@app.route('/update-profile-picture', methods=['POST', 'DELETE'])
@require_session_key
def update_profile_picture():
    if request.method == 'POST':
        user = User.query.get(g.user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        if 'profile_picture' not in request.files:
            return jsonify({"error": "No file part in the request"}), 400

        file = request.files['profile_picture']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400

        if file and allowed_file(file.filename):
            # Sanitize the filename for security
            filename = secure_filename(f"{user.username}_{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Update user record in the database
            user.profile_picture = file_path
            db.session.commit()

            return jsonify({"message": "Profile picture updated successfully", "path": file_path}), 200

        return jsonify({"error": "Invalid file format"}), 400
    elif request.method == 'DELETE':
        user = User.query.get(g.user_id)
        if not user:
            print("user not found")
            return jsonify({"error": "User not found"}), 404

        if user.profile_picture:
            os.remove(user.profile_picture)
            user.profile_picture = None
            db.session.commit()
            return jsonify({"message": "Profile picture deleted successfully!"}), 200

        return jsonify({"error": "No profile picture to delete"}), 400
    
@app.route('/allow-sharing', methods=['PUT'])
@require_session_key
def allow_sharing():
    data = request.json
    if not data or 'allows_sharing' not in data:
        return jsonify({"error": "Invalid request"}), 400

    user = User.query.get(g.user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    try:
        user.allows_sharing = data['allows_sharing']
        db.session.commit()
        return jsonify({"message": "Sharing preference updated successfully!"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error updating sharing preference: {e}")
        return jsonify({"error": "Failed to update sharing preference"}), 500
    
# Admin

@app.route('/admin', methods=['GET', 'DELETE', 'PUT'])
@require_session_key
def admin():
    user = User.query.get(g.user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    
    if user.role != "admin":
        return jsonify({"error": "Insufficient permissions"}), 400

    if request.method == 'GET':
        # Fetch users and messages
        users = User.query.with_entities(User.id, User.username, User.profile_picture, User.allows_sharing, User.role).all()
        messages = Messages.query.with_entities(Messages.id, Messages.email, Messages.message).all()
        return jsonify({
            "users": [user._asdict() for user in users],
            "messages": [message._asdict() for message in messages],
        })

    if request.method == 'DELETE':
        data = request.json
        target = data.get('target')
        target_type = data.get('type')

        if target_type == 'message':
            message = Messages.query.get(target)
            if not message:
                return jsonify({"error": "Message not found"}), 404
            db.session.delete(message)
            db.session.commit()
            return jsonify({"message": "Message deleted successfully"}), 200

        elif target_type == 'user':
            user_to_delete = User.query.get(target)
            if not user_to_delete:
                return jsonify({"error": "User not found"}), 404
            db.session.delete(user_to_delete)
            db.session.commit()
            return jsonify({"message": "User deleted successfully"}), 200

        return jsonify({"error": "Invalid target type"}), 400

    if request.method == 'PUT':
        data = request.json
        target_user_id = data.get('user_id')
        new_role = data.get('new_role')

        user_to_update = User.query.get(target_user_id)
        if not user_to_update:
            return jsonify({"error": "User not found"}), 404

        if new_role not in ["user", "admin"]:
            return jsonify({"error": "Invalid role value"}), 400

        user_to_update.role = new_role
        db.session.commit()
        return jsonify({"message": f"Role updated to {new_role} for user {target_user_id}"}), 200
    
@app.route('/admin/dump', methods=['POST'])
@require_session_key
def admin_dump():
    user = User.query.get(g.user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    if user.role != "admin":
        return jsonify({"error": "Insufficient permissions"}), 403
    
    if not user.database_dump_tag:
        return jsonify({"error": "Insufficient permissions"}), 403

    data = request.json
    password = data.get("password")

    if not bcrypt.check_password_hash(user.password, password):
        return jsonify({"error": "Incorrect password"}), 400

    # Fetch raw database data
    users = User.query.with_entities(
        User.id, User.username, User.profile_picture, User.allows_sharing, User.role
    ).all()
    messages = Messages.query.with_entities(Messages.id, Messages.email, Messages.message).all()
    notes = Note.query.with_entities(Note.id, Note.title, Note.tag, Note.note, Note.user_id)

    # Prepare data for dumping
    dump_data = {
        "users": [user._asdict() for user in users],
        "messages": [message._asdict() for message in messages],
        "notes": [note._asdict() for note in notes],
    }

    # Return as downloadable JSON file
    response = make_response(json.dumps(dump_data, indent=4))
    response.headers["Content-Disposition"] = "attachment; filename=database_dump.json"
    response.headers["Content-Type"] = "application/json"
    return response

# Authentication

@app.route('/signup', methods=['POST'])
def signup():
    data = request.json
    hashed_password = bcrypt.generate_password_hash(data['password']).decode('utf-8')
    try:
        user = User(username=data['username'], password=hashed_password)
        db.session.add(user)
        db.session.commit()
        return jsonify({"message": "User created successfully!"}), 201
    except:
        return jsonify({"error": "Username already exists"}), 400

@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "Invalid request data"}), 400

        lasting_key = data.get('lasting_key')
        if lasting_key:
            user = User.query.filter_by(lasting_key=lasting_key).first()
            if user:
                key = generate_session_key(user.id)
                return jsonify({"message": "Login successful!", "session_key": key, "user_id": user.id}), 200
            return jsonify({"error": "Invalid lasting key"}), 400

        # Only check for username and password if lasting_key is not provided
        username = data.get('username')
        password = data.get('password')
        if not username or not password:
            return jsonify({"error": "Missing username or password"}), 400

        user = User.query.filter_by(username=username).first()
        if not user or not bcrypt.check_password_hash(user.password, password):
            return jsonify({"error": "Invalid credentials"}), 400

        key = generate_session_key(user.id)
        if data.get('keep_login'):
            new_lasting_key = secrets.token_hex(32)
            user.lasting_key = new_lasting_key
            db.session.commit()
            return jsonify({
                "message": "Login successful!",
                "session_key": key,
                "user_id": user.id,
                "lasting_key": new_lasting_key
            }), 200

        return jsonify({"message": "Login successful!", "session_key": key, "user_id": user.id}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/logout', methods=['POST'])
@require_session_key
def logout():
    auth_header = request.headers.get("Authorization")
    key = auth_header.split("Bearer ")[1]
    del session_keys[key]
    return jsonify({"message": "Logged out successfully!"}), 200

@app.route('/test-session', methods=['GET'])
@require_session_key
def test_session():
    return jsonify({"message": "Session is valid!"}), 200

# Personal notes

@app.route('/notes', methods=['GET', 'POST'])
@require_session_key
def manage_notes():
    if request.method == 'POST':
        data = request.json
        title = data.get('title')  # Get the title if provided, else None
        note = data['note']
        tag = data.get('tag')  # Get the tag if provided, else None
        new_note = Note(user_id=g.user_id, title=title, note=note, tag=tag)
        db.session.add(new_note)
        db.session.commit()
        return jsonify({"message": "Note added successfully!"}), 201
    else:
        notes = Note.query.filter_by(user_id=g.user_id).all()
        sanitized_notes = [{"id": note.id, "title": note.title, "note": note.note, "tag": note.tag} for note in notes]
        return jsonify(sanitized_notes)

@app.route('/notes/<int:note_id>', methods=['PUT', 'DELETE'])
@require_session_key
def update_delete_note(note_id):
    note = Note.query.get(note_id)
    if not note or note.user_id != g.user_id:
        return jsonify({"error": "Note not found"}), 404

    if request.method == 'PUT':
        data = request.json
        note.title = data.get('title')  # Update the title if provided, else None
        note.note = data['note']
        note.tag = data.get('tag')  # Update the tag if provided, else None
        db.session.commit()
        return jsonify({"message": "Note updated successfully!"}), 200
    elif request.method == 'DELETE':
        db.session.delete(note)
        db.session.commit()
        return jsonify({"message": "Note deleted successfully!"}), 200
    
#---------------------------------Battleship routes--------------------------------

# --- Create Game ---
@app.route('/create', methods=['POST'])
def create_game():
    data = request.json
    player_name = data.get('playerName')
    play_bot = data.get('playAgainstBot', False)
    if not player_name:
        return jsonify({"error": "Missing player name"}), 400

    game_code = generate_game_code()
    game = {
        "players": {
            "player1": {
                "name": player_name,
                "ships": None,
                "hits": [],
                "misses": [],
                "incoming_misses": []
            },
            "player2": None  # To be filled either by join or by bot creation.
        },
        "status": "waiting",  # waiting -> placing -> battle -> gameover
        "turn": None,
        "winner": None
    }

    if play_bot:
        # Create bot player with pre-generated random ship placements.
        bot_ships = generate_bot_ships()
        game["players"]["player2"] = {
            "name": "Bot",
            "ships": bot_ships,
            "hits": [],
            "misses": [],
            "incoming_misses": []
        }
        # With a bot, both players are present from the start.
        game["status"] = "placing"
    games[game_code] = game
    return jsonify({"gameCode": game_code})

# --- Join Game ---
@app.route('/join', methods=['POST'])
def join_game():
    data = request.json
    player_name = data.get('playerName')
    game_code = data.get('gameCode')
    if not player_name or not game_code:
        return jsonify({"error": "Missing player name or game code"}), 400

    # Ensure the game code letters are uppercase.
    game_code = ''.join(char.upper() if char.isalpha() else char for char in game_code)

    if game_code not in games:
        return jsonify({"error": "Invalid game code"}), 400

    game = games[game_code]
    if game["players"]["player2"] is not None:
        # If playing against a bot, joining is not allowed.
        if game["players"]["player2"]["name"] == "Bot":
            return jsonify({"error": "Cannot join a bot game"}), 400
        return jsonify({"error": "Game already has 2 players"}), 400

    game["players"]["player2"] = {
        "name": player_name,
        "ships": None,
        "hits": [],
        "misses": [],
        "incoming_misses": []
    }
    game["status"] = "placing"
    return jsonify({"message": "Joined game", "gameCode": game_code})

# --- Place Ships ---
@app.route('/place_ships', methods=['POST'])
def place_ships():
    data = request.json
    game_code = data.get("gameCode")
    player = data.get("player")  # Expected: "player1" or "player2"
    ships = data.get("ships")    # List of ship objects with positions
    if not game_code or not player or ships is None:
        return jsonify({"error": "Missing data"}), 400
    if game_code not in games:
        return jsonify({"error": "Invalid game code"}), 400

    # Ensure each ship has a "sunk" flag.
    for ship in ships:
        if "sunk" not in ship:
            ship["sunk"] = False

    game = games[game_code]
    if player not in game["players"]:
        return jsonify({"error": "Invalid player"}), 400

    game["players"][player]["ships"] = ships

    # Check if both players have placed their ships.
    p1_ships = game["players"]["player1"]["ships"]
    p2_ships = game["players"]["player2"]["ships"] if game["players"]["player2"] else None

    if p1_ships and p2_ships:
        game["status"] = "battle"
        # Randomly choose who starts.
        game["turn"] = "player1" if random.random() < 0.5 else "player2"
        # If the bot gets the first turn, have it move.
        if game["turn"] == "player2" and game["players"]["player2"]["name"] == "Bot":
            time.sleep(0.5)
            bot_move(game_code)
    return jsonify({"message": "Ships placed", "status": game["status"]})

# --- Fire (Make a Move) ---
@app.route('/fire', methods=['POST'])
def fire():
    data = request.json
    game_code = data.get("gameCode")
    player = data.get("player")  # "player1" or "player2"
    x = data.get("x")
    y = data.get("y")
    if not game_code or not player or x is None or y is None:
        return jsonify({"error": "Missing data"}), 400
    if game_code not in games:
        return jsonify({"error": "Invalid game code"}), 400

    game = games[game_code]
    if game["status"] != "battle":
        return jsonify({"error": "Game is not in battle phase"}), 400

    if game["turn"] != player:
        return jsonify({"error": "Not your turn"}), 400

    result = process_fire(game, player, x, y)

    # If it is now the bot's turn, call bot_move.
    if (game["status"] == "battle" and game["turn"] == "player2" and
            game["players"]["player2"]["name"] == "Bot"):
        time.sleep(0.5)
        bot_move(game_code)

    return jsonify(result)

# --- Get Game State (for polling) ---
@app.route('/game_state', methods=['GET'])
def game_state():
    game_code = request.args.get("gameCode")
    if not game_code or game_code not in games:
        return jsonify({"error": "Invalid game code"}), 400
    game = games[game_code]
    response = {
        "players": game["players"],
        "status": game["status"],
        "turn": game["turn"],
        "winner": game["winner"],
        "opponentJoined": game["players"]["player2"] is not None
    }
    return jsonify(response)

# --- Stop Game ---
@app.route('/stop', methods=['POST'])
def stop():
    game_code = request.args.get("gameCode")
    if not game_code or game_code not in games:
        return jsonify({"error": "Invalid game code"}), 400

    del games[game_code]
    return jsonify({"message": f"Game {game_code} stopped"})

# --- Spectate State ---
@app.route('/spectate_state', methods=['GET'])
def spectate_state():
    game_code = request.args.get("gameCode")
    if not game_code or game_code not in games:
        return jsonify({"error": "Invalid game code"}), 400

    game = games[game_code]
    filtered_players = {}
    for player, pdata in game["players"].items():
        if pdata is None:
            filtered_players[player] = None
        else:
            # Only show sunk ships.
            sunk_ships = []
            if pdata.get("ships"):
                for ship in pdata["ships"]:
                    if ship.get("sunk"):
                        sunk_ships.append(ship)
            filtered_players[player] = {
                "name": pdata["name"],
                "hits": pdata.get("hits", []),
                "misses": pdata.get("misses", []),
                "sunk_ships": sunk_ships
            }

    winner_name = None
    if game["winner"]:
        winning_player = game["players"].get(game["winner"])
        if winning_player:
            winner_name = winning_player["name"]

    response = {
        "players": filtered_players,
        "status": game["status"],
        "turn": game["turn"],
        "winner": winner_name,
        "opponentJoined": game["players"]["player2"] is not None
    }
    return jsonify(response)

# --- List Games ---
@app.route('/list_games', methods=['GET'])
def list_games():
    ongoing_games = []
    for game_code, game in games.items():
        if game.get("status") != "gameover":
            ongoing_games.append({
                "gameCode": game_code,
                "status": game.get("status"),
                "opponentJoined": game["players"]["player2"] is not None
            })
    return jsonify({"games": ongoing_games})

# ---------------------------------Run the app--------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0")
