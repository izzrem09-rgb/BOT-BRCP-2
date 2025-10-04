# bot_gremio.py
import os
import re
import sqlite3
import threading
import discord
import datetime
from discord.ext import commands
from discord.ui import View, Button
from collections import Counter
from typing import Optional, List, Dict, Any
from discord import TextChannel, Guild
from flask import Flask
from threading import Thread
from discord import app_commands


app = Flask("")

@app.route("/")
def home():
    return "Bot activo ✅"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()

# ------------------ CONFIG ------------------
TOKEN = os.getenv("DISCORD_TOKEN")
APPROVAL_CHANNEL_ID = int(
    os.getenv("APPROVAL_CHANNEL_ID", "1423525053366009988"))
BALANCES_CHANNEL_ID = os.getenv("BALANCES_CHANNEL_ID", "1423528491906764841")
ADMIN_ROLE_IDS = [
    1398647954679660649,  # Rol admin principal
    1398672523876372583,
    1398674257961029703, # Otro rol admin
]
LOGS_CHANNEL_ID = os.getenv("LOGS_CHANNEL_ID", "1423525483298947223")

if BALANCES_CHANNEL_ID:
    BALANCES_CHANNEL_ID = int(BALANCES_CHANNEL_ID)
if LOGS_CHANNEL_ID:
    LOGS_CHANNEL_ID = int(LOGS_CHANNEL_ID)

COMMAND_PREFIX = "!"

# ------------------ VALORES POR NÚMERO ------------------
# Usamos claves tipo str porque message.content.split() y re.findall devuelven strings.
NUMBER_VALUES: Dict[str, int] = {
    "1": 925000,
    "2": 1037000,
    "3": 744000,
    "4": 838000,
    "5": 696000,
    "6": 1094000,
    "7": 1393000,
    "8": 857000,
    "9": 1018000,
    "10": 824000,
    "11": 831000,
    "12": 1259000,
    "13": 1284000,
    "14": 1299000,
    "15": 1461000,
    "16": 950000,
    "17": 675000,
    "18": 682000,
    "19": 740000,
    "20": 1243000,
    "21": 1768000,
    "22": 1126000,
    "23": 1517000,
    "24": 1653000,
    "25": 3179000,
    "26": 1003000,
    "27": 2535000,
    "28": 6000000,
    "29": 4000000
}
NUMBER_LIST: List[str] = list(NUMBER_VALUES.keys())

# ------------------ BOT / INTENTS ------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ DATABASE SEGURA ------------------
DB_PATH = os.getenv("DB_PATH", "balances.db")
db_lock = threading.Lock()

# Crear tabla si no existe
with db_lock:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS balances (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            balance INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    conn.commit()
    conn.close()


def get_balance(guild_id: int, user_id: int) -> int:
    """Devuelve el balance actual de un usuario, 0 si no existe."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id)
        )
        row = cur.fetchone()
        conn.close()
    return int(row["balance"]) if row else 0


def set_balance(guild_id: int, user_id: int, amount: int):
    """Establece el balance exacto de un usuario (reemplaza lo anterior)."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO balances (guild_id, user_id, balance)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = excluded.balance
        """, (guild_id, user_id, amount))
        conn.commit()
        conn.close()


def add_balance(guild_id: int, user_id: int, amount: int):
    """Suma de manera atómica el balance, segura para concurrencia."""
    if amount <= 0:
        return
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO balances (guild_id, user_id, balance)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = balance + excluded.balance
        """, (guild_id, user_id, amount))
        conn.commit()
        conn.close()


def remove_balance(guild_id: int, user_id: int, amount: int):
    """Resta balance de manera atómica, no permite negativos."""
    if amount <= 0:
        return
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE balances
            SET balance = MAX(balance - ?, 0)
            WHERE guild_id = ? AND user_id = ?
        """, (amount, guild_id, user_id))
        conn.commit()
        conn.close()


def top_balances(guild_id: int, limit: int = 250):
    """Devuelve lista de usuarios ordenada por balance descendente."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT user_id, balance FROM balances WHERE guild_id = ? ORDER BY balance DESC LIMIT ?",
            (guild_id, limit)
        )
        rows = cur.fetchall()
        conn.close()
    return rows

# ------------------ COLAS Y MENSAJES FIJOS ------------------
approval_queue: List[Dict[str, Any]] = []
approval_message: Optional[discord.Message] = None
balances_messages: List[discord.Message] = []


# ------------------ VIEW (Botones) ------------------
class ApprovalView(View):

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            Button(style=discord.ButtonStyle.success,
                   label="Aprobar",
                   custom_id="approve"))
        self.add_item(
            Button(style=discord.ButtonStyle.danger,
                   label="Rechazar",
                   custom_id="reject"))
        self.add_item(
            Button(style=discord.ButtonStyle.secondary,
                   label="Pendiente",
                   custom_id="pending"))


# ------------------ LOGS ------------------
async def send_log(guild: Guild, text: str):
    if not LOGS_CHANNEL_ID:
        return
    channel = guild.get_channel(LOGS_CHANNEL_ID)
    if isinstance(channel, TextChannel):
        try:
            await channel.send(text)
        except Exception as e:
            print("Error enviando log:", e)


# ------------------ MENSAJES FIJOS / ACTUALIZAR ------------------
async def ensure_approval_message(
        guild: Optional[Guild]) -> Optional[discord.Message]:
    global approval_message
    if guild is None:
        return None
    channel = guild.get_channel(APPROVAL_CHANNEL_ID)
    if not isinstance(channel, TextChannel):
        return None

    try:
        async for msg in channel.history(limit=50):
            if msg.author == bot.user and (msg.embeds
                                           or msg.content.startswith("📥")):
                approval_message = msg
                return approval_message
    except Exception:
        pass

    try:
        approval_message = await channel.send(
            "📥 Iniciando sistema de regear...")
        return approval_message
    except Exception as e:
        print("No se pudo crear approval_message:", e)
        return None


async def update_approval_message(guild: Optional[Guild]):
    global approval_message
    if guild is None:
        return
    channel = guild.get_channel(APPROVAL_CHANNEL_ID)
    if not isinstance(channel, TextChannel):
        return

    if approval_message is None:
        approval_message = await ensure_approval_message(guild)
        if approval_message is None:
            return

    if not approval_queue:
        embed = discord.Embed(title="🎉 Todos los regear han sido gestionados",
                              description="No quedan solicitudes pendientes.",
                              color=discord.Color.green())
        try:
            await approval_message.edit(embed=embed, view=None)
        except Exception as e:
            print("Error editando approval_message (vacío):", e)
        return

    current = approval_queue[0]
    user_id = current.get("user_id")
    numbers_counter: Counter = current.get("numbers_counter", Counter())
    total_value: int = current.get("total_value", 0)
    attachments = current.get("attachments", [])

    numbers_text = ", ".join([
        f"{num} x{count}" for num, count in numbers_counter.items()
    ]) if numbers_counter else "Ninguno"

    embed = discord.Embed(
        title="📥 Solicitud de Regear en Revisión",
        description=
        f"**Jugador:** <@{user_id}>\n**Números:** {numbers_text}\n**Total:** {total_value:,} silver",
        color=discord.Color.blue())

    if attachments:
        try:
            first = attachments[0]
            url = first.url if hasattr(first, "url") else str(first)
            embed.set_image(url=url)
        except Exception as e:
            print("No se pudo establecer imagen principal:", e)
        for i, att in enumerate(attachments[1:], start=2):
            try:
                url = att.url if hasattr(att, "url") else str(att)
                embed.add_field(name=f"📸 Imagen {i}", value=url, inline=False)
            except Exception as e:
                print("No se pudo añadir campo de imagen:", e)

    try:
        await approval_message.edit(embed=embed, view=ApprovalView())
    except Exception as e:
        print("Error editando approval_message:", e)
        try:
            approval_message = await channel.send(embed=embed,
                                                  view=ApprovalView())
        except Exception as e2:
            print("Segundo intento fallo al enviar approval_message:", e2)


# ------------------ BALANCES FIJOS CON PÁGINAS ------------------
async def ensure_balances_messages(guild: Optional[Guild]):
    """Reutiliza embeds existentes de balances al iniciar el bot"""
    global balances_messages
    if guild is None or not BALANCES_CHANNEL_ID:
        return
    channel = guild.get_channel(BALANCES_CHANNEL_ID)
    if not isinstance(channel, TextChannel):
        return

    try:
        async for msg in channel.history(limit=50):
            if msg.author == bot.user and msg.embeds:
                balances_messages.append(msg)
        balances_messages.sort(key=lambda m: m.id)  # mantener orden por ID
    except Exception:
        pass


async def update_balances_message(guild: Optional[Guild]):
    global balances_messages
    if guild is None or not BALANCES_CHANNEL_ID:
        return
    channel = guild.get_channel(BALANCES_CHANNEL_ID)
    if not isinstance(channel, TextChannel):
        return

    rows = top_balances(guild.id, limit=250)
    if not rows:
        description = "No hay datos aún."
        if balances_messages:
            try:
                await balances_messages[0].edit(embed=discord.Embed(
                    title="📊 Balances del Gremio",
                    description=description,
                    color=discord.Color.blurple()
                ).set_footer(
                    text=
                    f"Solo visible para Admins – Última actualización {datetime.datetime.now().strftime('%H:%M:%S')}"
                ))
            except Exception:
                pass
        else:
            try:
                msg = await channel.send(embed=discord.Embed(
                    title="📊 Balances del Gremio",
                    description=description,
                    color=discord.Color.blurple()
                ).set_footer(
                    text=
                    f"Solo visible para Admins – Última actualización {datetime.datetime.now().strftime('%H:%M:%S')}"
                ))
                balances_messages.append(msg)
            except Exception:
                pass
        return

    page_size = 50
    total_pages = (len(rows) + page_size - 1) // page_size

    while len(balances_messages) < total_pages:
        try:
            msg = await channel.send("Cargando balances...")
            balances_messages.append(msg)
        except Exception:
            pass
    while len(balances_messages) > total_pages:
        msg_to_remove = balances_messages.pop()
        try:
            await msg_to_remove.delete()
        except Exception:
            pass

    for page_index in range(total_pages):
        chunk = rows[page_index * page_size:(page_index + 1) * page_size]
        lines = [
            f"**{i + 1}.** <@{row['user_id']}> — {int(row['balance']):,} silver"
            for i, row in enumerate(chunk, start=page_index * page_size)
        ]
        description = "\n".join(lines)
        embed = discord.Embed(
            title=f"📊 Balances del Gremio (página {page_index + 1})",
            description=description,
            color=discord.Color.blurple())
        embed.set_footer(
            text=
            f"Solo visible para Admins – Última actualización {datetime.datetime.now().strftime('%H:%M:%S')}"
        )
        try:
            await balances_messages[page_index].edit(embed=embed)
        except Exception as e:
            print("Error editando embed de balances:", e)


# ------------------ PROCESO DE APROBACION ------------------
async def process_approval(interaction: discord.Interaction,
                           user_id: Optional[int], numbers_counter: Counter,
                           total_value: int, status: str,
                           original_channel_id: Optional[int], attachments,
                           original_message_id: Optional[int]):

    guild = interaction.guild
    if guild is None or user_id is None or original_channel_id is None or original_message_id is None:
        try:
            await interaction.response.send_message(
                "❌ Error: servidor o datos incompletos.", ephemeral=True)
        except Exception:
            pass
        return

    member = guild.get_member(user_id)
    if status == "Aprobado":
        add_balance(guild.id, user_id, total_value)
        emoji = "✅"
        await send_log(
            guild,
            f"✅ Aprobado: <@{user_id}> +{total_value:,} silver (números: {numbers_counter})"
        )
    elif status == "Rechazado":
        emoji = "❌"
        await send_log(
            guild, f"❌ Rechazado: <@{user_id}> (números: {numbers_counter})")
    else:
        emoji = "⏳"
        await send_log(
            guild, f"⏳ Pendiente: <@{user_id}> (números: {numbers_counter})")

    numbers_text = ", ".join([
        f"{num} x{count}" for num, count in numbers_counter.items()
    ]) if numbers_counter else "Ninguno"
    mention = member.mention if member else f"<@{user_id}>"

    try:
        orig_channel = guild.get_channel(original_channel_id)
        if isinstance(orig_channel, TextChannel):
            try:
                orig_msg = await orig_channel.fetch_message(original_message_id
                                                            )
                try:
                    await orig_msg.add_reaction(emoji)
                except Exception:
                    pass
                try:
                    await orig_msg.reply(
                        f"{mention} tu regear ha sido **{status}**.\n"
                        f"Números: {numbers_text}\n"
                        f"Total agregado: {total_value:,} silver")
                except Exception:
                    pass
            except Exception:
                pass

    except Exception as e:
        print("Error tratando mensaje original:", e)

    try:
        await interaction.response.send_message(
            f"✅ Solicitud de {mention} procesada: {status}", ephemeral=True)
    except Exception:
        pass

    if approval_queue:
        approval_queue.pop(0)

    await update_approval_message(guild)
    await update_balances_message(guild)


# ------------------ EVENTOS ------------------
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")

    for g in bot.guilds:
        try:
            await ensure_approval_message(g)
            await update_approval_message(g)
            await ensure_balances_messages(g)
            await update_balances_message(g)
        except Exception as e:
            print("Error inicializando guild:", e)

    # Sincronizar comandos slash solo en tu servidor
    try:
        guild_obj = discord.Object(id=1398647954616619038)
        synced = await bot.tree.sync(guild=guild_obj)
        print(f"🌐 Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print("❌ Error sincronizando comandos:", e)
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Solo atender mensajes en el canal de regear
    REGEAR_CHANNEL_ID = 1398647955178917934
    if message.channel.id != REGEAR_CHANNEL_ID:
        # ⚠️ IMPORTANTE: no procesamos nada más, solo comandos
        await bot.process_commands(message)
        return

    # Extraer números 1..29 como strings
    numbers = re.findall(r'\b(?:[1-9]|1[0-9]|2[0-9])\b', message.content)
    numbers_counter: Counter = Counter(numbers)
    total_value = sum(NUMBER_VALUES.get(num, 0) * count for num, count in numbers_counter.items())

    # Revisar adjuntos
    attachments_list = list(message.attachments)

    # Solo meter en cola si hay números válidos o imágenes en el canal correcto
    if total_value > 0 or attachments_list:
        approval_queue.append({
            "user_id": message.author.id,
            "numbers_counter": numbers_counter,
            "total_value": total_value,
            "original_channel_id": message.channel.id,
            "attachments": attachments_list,
            "original_message_id": message.id
        })

        if len(approval_queue) == 1:
            await update_approval_message(message.guild)

    # Siempre dejar pasar los comandos
    await bot.process_commands(message)


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component or not interaction.data:
        return

    custom_id = interaction.data.get("custom_id")
    guild = interaction.guild
    if guild is None:
        try:
            await interaction.response.send_message("❌ Este comando no puede usarse en DM.", ephemeral=True)
        except Exception:
            pass
        return

    member = guild.get_member(interaction.user.id)
    member_roles_ids = [int(r.id) for r in member.roles] if member else []
    if not any(rid in ADMIN_ROLE_IDS for rid in member_roles_ids):
        try:
            await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
        except Exception:
            pass
        return

    if not approval_queue:
        try:
            await interaction.response.send_message("❌ No hay solicitudes en cola.", ephemeral=True)
        except Exception:
            pass
        return

    current_req = approval_queue[0]
    user_id = current_req.get("user_id")
    numbers_counter = current_req.get("numbers_counter", Counter())
    total_value = current_req.get("total_value", 0)
    original_channel_id = current_req.get("original_channel_id")
    attachments = current_req.get("attachments", [])
    original_message_id = current_req.get("original_message_id")

    if custom_id == "approve":
        await process_approval(interaction, user_id, numbers_counter, total_value,
                               "Aprobado", original_channel_id, attachments, original_message_id)
    elif custom_id == "reject":
        await process_approval(interaction, user_id, Counter(), 0,
                               "Rechazado", original_channel_id, attachments, original_message_id)
    elif custom_id == "pending":
        await process_approval(interaction, user_id, Counter(), 0,
                               "Pendiente", original_channel_id, attachments, original_message_id)



# ------------------ COMANDOS SLASH ------------------
# ------------------ addbal ------------------
@bot.tree.command(name="addbal", description="Añadir silver a un jugador (Admin)")
@app_commands.describe(member="Jugador", amount="Cantidad de silver")
async def addbal(interaction: discord.Interaction, member: discord.Member, amount: int):
    # Validar que el comando se ejecute en un servidor
    if interaction.guild is None:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    # Validar cantidad positiva
    if amount <= 0:
        await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
        return

    # Obtener al member que ejecuta el comando
    executor: discord.Member | None = interaction.guild.get_member(interaction.user.id)
    if executor is None:
        await interaction.response.send_message("❌ No se pudo verificar tu rol.", ephemeral=True)
        return

    # Verificar rol de administrador
    member_roles_ids = [r.id for r in executor.roles]
    if not any(rid in ADMIN_ROLE_IDS for rid in member_roles_ids):
        await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
        return

    # Añadir balance de manera atómica y obtener balance actualizado
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO balances (guild_id, user_id, balance) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = balance + ?",
            (interaction.guild.id, member.id, amount, amount)
        )
        conn.commit()
        cur.execute(
            "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?",
            (interaction.guild.id, member.id)
        )
        row = cur.fetchone()
        conn.close()

    new_balance = int(row[0])

    # Enviar mensaje de confirmación
    await interaction.response.send_message(
        f"✅ {amount:,} silver añadidos a {member.mention}. Balance actual: {new_balance:,}"
    )

    # Enviar log y actualizar balances
    await send_log(interaction.guild, f"ADMIN {executor} añadió {amount:,} silver a <@{member.id}>")
    await update_balances_message(interaction.guild)

# ------------------ balremove ------------------
# ------------------ balremove (corregido) ------------------
@bot.tree.command(name="balremove", description="Remover silver de un jugador (Admin)")
@app_commands.describe(member="Jugador", amount="Cantidad de silver a remover")
async def balremove(interaction: discord.Interaction, member: discord.Member, amount: int):
    # Validar que el comando se ejecute en un servidor
    if interaction.guild is None:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    # Validar cantidad positiva
    if amount <= 0:
        await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
        return

    # Obtener al member que ejecuta el comando
    executor: discord.Member | None = interaction.guild.get_member(interaction.user.id)
    if executor is None:
        await interaction.response.send_message("❌ No se pudo verificar tu rol.", ephemeral=True)
        return

    # Verificar rol de administrador
    member_roles_ids = [r.id for r in executor.roles]
    if not any(rid in ADMIN_ROLE_IDS for rid in member_roles_ids):
        await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
        return

    # Remover balance de manera segura y obtener balance actualizado
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Obtener balance actual
        cur.execute(
            "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?",
            (interaction.guild.id, member.id)
        )
        row = cur.fetchone()
        current_balance = int(row[0]) if row else 0

        # Calcular nuevo balance
        new_balance = max(current_balance - amount, 0)

        # Actualizar balance
        cur.execute(
            "INSERT INTO balances (guild_id, user_id, balance) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = ?",
            (interaction.guild.id, member.id, new_balance, new_balance)
        )
        conn.commit()
        conn.close()

    # Enviar mensaje de confirmación
    await interaction.response.send_message(
        f"✅ {amount:,} silver removidos de {member.mention}. Balance actual: {new_balance:,}"
    )

    # Enviar log y actualizar balances
    await send_log(interaction.guild, f"ADMIN {executor} removió {amount:,} silver de <@{member.id}>")
    await update_balances_message(interaction.guild)

# ------------------ balance ------------------
@bot.tree.command(name="balance", description="Ver tu balance o el de otro jugador con ranking")
@app_commands.describe(member="Jugador (opcional)")
async def balance(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    if member is None:
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("❌ No se pudo encontrar tu información en el servidor.", ephemeral=True)
            return

    user_id = member.id
    balance_val = get_balance(interaction.guild.id, user_id)

    # Obtener ranking global usando top_balances (con lock)
    all_balances = top_balances(interaction.guild.id, limit=1000)  # límite grande
    ranking = None
    for idx, row in enumerate(all_balances, start=1):
        if row["user_id"] == user_id:
            ranking = idx
            break

    if ranking is None:
        ranking = "N/A"

    embed = discord.Embed(
        title=f"💰 Balance de {member.display_name}",
        color=discord.Color.gold()
    )
    embed.add_field(name="Balance", value=f"`{balance_val:,} silver`", inline=False)
    embed.add_field(name="Ranking", value=f"`#{ranking}`", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Solicitado por {interaction.user.display_name} • {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")

    await interaction.response.send_message(embed=embed)

# ------------------ top ------------------
@bot.tree.command(name="top", description="Ver el top 10 de balances")
async def top(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    rows = top_balances(interaction.guild.id, limit=10)
    if not rows:
        await interaction.response.send_message("🏆 No hay jugadores con balance todavía.")
        return

    msg = "\n".join([
        f"{i+1}. <@{r['user_id']}>: {int(r['balance']):,} silver"
        for i, r in enumerate(rows)
    ])
    await interaction.response.send_message(f"🏆 Top 10 jugadores:\n{msg}")

# ------------------ pagar ------------------
@bot.tree.command(name="pagar", description="Pagar a un jugador y dejar su balance en 0")
@app_commands.describe(member="Jugador a pagar")
async def pagar(interaction: discord.Interaction, member: discord.Member):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    # Convertir el usuario que ejecuta el comando en Member
    executor = interaction.guild.get_member(interaction.user.id)
    if executor is None:
        await interaction.response.send_message("❌ No se pudo identificar tu usuario en el servidor.", ephemeral=True)
        return

    # Obtener lista de roles del ejecutor
    member_roles_ids = [r.id for r in executor.roles]

    # Verificar roles de administrador (varios permitidos)
    if not any(rid in ADMIN_ROLE_IDS for rid in member_roles_ids):
        await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
        return

    # Obtener balance, pagar y resetear
    total = get_balance(interaction.guild.id, member.id)
    set_balance(interaction.guild.id, member.id, 0)
    await interaction.response.send_message(
        f"✅ Se pagó `{total:,} silver` a {member.mention}. Ahora su balance es 0."
    )

    # Pasar guild seguro
    await send_log(interaction.guild, f"ADMIN {executor} pagó {total:,} silver a <@{member.id}>")
    await update_balances_message(interaction.guild)

# ------------------ transferir ------------------
@bot.tree.command(name="transferir", description="Transferir silver a otro jugador")
@app_commands.describe(member="Jugador receptor", amount="Cantidad a transferir")
async def transferir(interaction: discord.Interaction, member: discord.Member, amount: int):
    if interaction.guild is None:
        await interaction.response.send_message("❌ Este comando solo puede usarse en un servidor.", ephemeral=True)
        return

    if amount <= 0:
        await interaction.response.send_message("❌ La cantidad debe ser mayor a 0.", ephemeral=True)
        return

    if get_balance(interaction.guild.id, interaction.user.id) < amount:
        await interaction.response.send_message("❌ No tienes suficiente balance para transferir.", ephemeral=True)
        return

    remove_balance(interaction.guild.id, interaction.user.id, amount)
    add_balance(interaction.guild.id, member.id, amount)

    await interaction.response.send_message(
        f"✅ {interaction.user.mention} transfirió `{amount:,} silver` a {member.mention}.\n"
        f"Tu nuevo balance: `{get_balance(interaction.guild.id, interaction.user.id):,} silver`\n"
        f"Balance de {member.mention}: `{get_balance(interaction.guild.id, member.id):,} silver`"
    )


    # Pasamos guild seguro
    await send_log(interaction.guild, f"{interaction.user} transfirió {amount:,} silver a <@{member.id}>")
    await update_balances_message(interaction.guild)


# ------------------ split ------------------
@bot.tree.command(name="split", description="Repartir silver entre jugadores")
@app_commands.describe(
    total="Cantidad total de silver a repartir",
    impuesto="Porcentaje de impuesto (default 19%)",
    silver_liquido="Silver líquido adicional (default 0)",
    reparacion="Silver usado para reparación (default 0)",
    jugadores="Menciona a los jugadores que recibirán el split"
)
async def split(interaction: discord.Interaction,
                total: int,
                jugadores: str,
                impuesto: int = 19,
                silver_liquido: int = 0,
                reparacion: int = 0):

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo puede ejecutarse en un servidor.", ephemeral=True
        )
        return

    # Extraer IDs de usuarios mencionados (discord menciona como <@ID>)
    import re
    ids = re.findall(r'\d{17,20}', jugadores)
    miembros: list[discord.Member] = []
    for uid in ids:
        member = interaction.guild.get_member(int(uid))
        if member:
            miembros.append(member)

    if not miembros:
        await interaction.response.send_message(
            "❌ No se encontraron jugadores válidos en las menciones.", ephemeral=True
        )
        return

    # Cálculos del split
    jugadores_count = len(miembros)
    impuesto_calc = int(total * (impuesto / 100))
    neto = total - impuesto_calc - reparacion + silver_liquido
    por_jugador = neto // jugadores_count

    # Repartir silver
    for m in miembros:
        add_balance(interaction.guild.id, m.id, por_jugador)

    # Embed principal con totales
    embed_principal = discord.Embed(title="⚔️ Loot Split", color=discord.Color.gold())
    embed_principal.add_field(name="💰 Total Bruto", value=f"{total:,}", inline=False)
    embed_principal.add_field(name=f"🏦 Impuesto ({impuesto}%)", value=f"-{impuesto_calc:,}", inline=False)
    embed_principal.add_field(name="🛠 Reparación", value=f"-{reparacion:,}", inline=False)
    embed_principal.add_field(name="💵 Silver Líquido", value=f"+{silver_liquido:,}", inline=False)
    embed_principal.add_field(name="📉 Total Neto", value=f"{neto:,}", inline=False)
    embed_principal.add_field(name="⚖️ Cada jugador recibe", value=f"{por_jugador:,}", inline=False)
    embed_principal.set_footer(text=f"Ejecutado por {interaction.user} • {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
    await interaction.response.send_message(embed=embed_principal)

    # Embeds de lista de jugadores (segmentados si hay muchos)
    MAX_CHARS = 2000
    chunk = []
    current_len = 0
    for m in miembros:
        line = f"{m.mention}\n"
        if current_len + len(line) > MAX_CHARS:
            embed = discord.Embed(
                title=f"💰 Distribución a jugadores ({len(chunk)} afectados)",
                description="".join(chunk) + f"\n💰 Balance añadido: {por_jugador:,}",
                color=discord.Color.light_grey()
            )
            embed.set_footer(text=f"Ejecutado por {interaction.user} • {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
            await interaction.followup.send(embed=embed)
            chunk = [line]
            current_len = len(line)
        else:
            chunk.append(line)
            current_len += len(line)

    if chunk:
        embed = discord.Embed(
            title=f"💰 Distribución a jugadores ({len(chunk)} afectados)",
            description="".join(chunk) + f"\n💰 Balance añadido: {por_jugador:,}",
            color=discord.Color.light_grey()
        )
        embed.set_footer(text=f"Ejecutado por {interaction.user} • {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
        await interaction.followup.send(embed=embed)

    # Log y actualización de balances
    await send_log(interaction.guild,
                   f"{interaction.user} hizo split: total {total:,}, impuesto {impuesto}%, reparación {reparacion:,}, líquido {silver_liquido:,}, jugadores {jugadores_count}")
    await update_balances_message(interaction.guild)

# ------------------ updatebalances ------------------
@bot.tree.command(name="updatebalances", description="Actualizar lista de balances")
async def updatebalances(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo puede ejecutarse en un servidor.", ephemeral=True
        )
        return

    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        await interaction.response.send_message("❌ No se pudo verificar tu rol.", ephemeral=True)
        return

    # Obtener roles del usuario
    member_roles_ids = [r.id for r in member.roles]

    # Verificar si tiene alguno de los roles admin
    if not any(rid in ADMIN_ROLE_IDS for rid in member_roles_ids):
        await interaction.response.send_message("❌ No tienes permisos.", ephemeral=True)
        return

    # Ejecutar actualización
    await update_balances_message(interaction.guild)
    try:
        await interaction.response.send_message("✅ Lista de balances actualizada correctamente.", ephemeral=True)
    except Exception:
        pass

# IDs de los desarrolladores
DESARROLLADORES_IDS = [682425081300779058, 466932448937377792]

@bot.tree.command(name="helpbot", description="Mostrar manual del bot")
async def helpbot(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Este comando solo puede ejecutarse en un servidor.", ephemeral=True
        )
        return

    # Construir lista de desarrolladores solo con @mención
    desarrolladores_texto_list = []
    for uid in DESARROLLADORES_IDS:
        dev_member = interaction.guild.get_member(uid)
        if dev_member:
            desarrolladores_texto_list.append(dev_member.mention)
        else:
            try:
                dev_user = await bot.fetch_user(uid)
                desarrolladores_texto_list.append(dev_user.mention)
            except Exception:
                desarrolladores_texto_list.append(f"<@{uid}>")

    desarrolladores_texto = " y ".join(desarrolladores_texto_list)

    # Crear embed
    embed = discord.Embed(
        title="📖 Manual del Bot de Regear & Balance",
        description="Guía completa del bot del gremio",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="⚔️ Sistema de Solicitudes de Regear",
        value=(
            "- Envía mensaje con números (del `1` al `29`) y/o imágenes → entra en cola de aprobación.\n"
            "- El canal de aprobación tiene un **embed fijo** que se actualiza con la solicitud actual.\n"
            "- El embed muestra números (que son roles en excel), total y las imágenes.\n"
            "- Botones de **Aprobar / Rechazar / Pendiente**.\n"
            "- Al procesar:\n"
            "   • Balance actualizado si es aprobado.\n"
            "   • Reacción automática en mensaje original: ✅ aprobado, ❌ rechazado, ⏳ pendiente.\n"
            "   • Responde al mensaje notificando el estado."
        ),
        inline=False
    )

    embed.add_field(
        name="💰 Sistema de Balance",
        value=(
            "__Admins:__\n"
            "`/addbal @jugador cantidad`\n"
            "`/balremove @jugador cantidad`\n"
            "`/pagar @jugador`\n\n"
            "__Todos:__\n"
            "`/balance` o `/bal`\n"
            "`/top`\n"
            "`/transferir @jugador cantidad`"
        ),
        inline=False
    )

    embed.add_field(
        name="⚖️ Sistema de Loot Split",
        value=(
            "`/split total [impuesto%] [silver_liquido] [reparacion] @jugadores...`\n\n"
            "- Detecta jugadores mencionados.\n"
            "- Impuesto configurable (default 19%).\n"
            "- Suma silver líquido si lo indicas.\n"
            "- Resta reparación si se indica.\n"
            "- Reparte entre los mencionados.\n"
            "- Deposita automáticamente.\n"
            "- Embed con detalles."
        ),
        inline=False
    )

    embed.add_field(
        name="🔒 Seguridad",
        value=(
            "- Solo usuarios con el rol configurado en `ADMIN` pueden aprobar/rechazar solicitudes.\n"
            "- Solo Admins pueden modificar balances o usar `/helpbot`.\n"
            "- Todos pueden usar `/balance`, `/top`, `/transferir`, `/split`."
        ),
        inline=False
    )

    # Campo al final con los desarrolladores mencionados
    embed.add_field(
        name=" Desarrolladores",
        value=desarrolladores_texto,
        inline=False
    )

    embed.set_footer(
        text="Bot oficial del gremio – Sistema de Regear, Balance y Loot Split"
    )

    await interaction.response.send_message(embed=embed)


# ------------------ RUN ------------------
if TOKEN is None:
    print("❌ ERROR: DISCORD_TOKEN no configurado en las variables de entorno.")
    exit(1)

if __name__ == "__main__":
    keep_alive()      # arranca el servidor HTTP en segundo plano
    print("🌐 Servidor keep_alive iniciado en http://0.0.0.0:8080")
    bot.run(TOKEN)    # arranca el bot (bloquea el hilo principal)
