import os
import logging
from typing import Optional

from supabase import create_client, Client

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import KeyboardButtonStyle
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# =========================================================
# 기본 설정
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

PORT = int(os.environ.get("PORT", "10000"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

WEBHOOK_PATH = "telegram-webhook"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN이 없습니다.")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL이 없습니다.")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY가 없습니다.")

if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL이 없습니다.")


# =========================================================
# 로그
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# Supabase
# =========================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)


# =========================================================
# 투표 선택지
# =========================================================

CHOICES = {
    "home": {
        "name": "홈 승",
        "emoji": "🏠",
        "style": KeyboardButtonStyle.GREEN,
    },
    "draw": {
        "name": "무승부",
        "emoji": "🤝",
        "style": KeyboardButtonStyle.PRIMARY,
    },
    "away": {
        "name": "원정 승",
        "emoji": "✈️",
        "style": KeyboardButtonStyle.DANGER,
    },
}


# =========================================================
# DB 관련 함수
# =========================================================

def create_poll(chat_id: int) -> int:
    """새로운 투표를 생성하고 poll_id를 반환"""

    # 기존 활성 투표 종료
    supabase.table("polls").update(
        {"active": False}
    ).eq(
        "chat_id", chat_id
    ).eq(
        "active", True
    ).execute()

    # 새 투표 생성
    result = supabase.table("polls").insert(
        {
            "chat_id": chat_id,
            "active": True,
        }
    ).select(
        "id"
    ).execute()

    return int(result.data[0]["id"])


def set_poll_message_id(
    poll_id: int,
    message_id: int,
):
    """투표 메시지 ID 저장"""

    supabase.table("polls").update(
        {
            "message_id": message_id
        }
    ).eq(
        "id", poll_id
    ).execute()


def get_active_poll(chat_id: int) -> Optional[dict]:
    """현재 활성 투표 가져오기"""

    result = supabase.table("polls").select(
        "id, chat_id, message_id, active"
    ).eq(
        "chat_id", chat_id
    ).eq(
        "active", True
    ).limit(1).execute()

    if not result.data:
        return None

    return result.data[0]


def get_user_vote(
    poll_id: int,
    user_id: int,
) -> Optional[str]:
    """사용자의 현재 선택"""

    result = supabase.table("votes").select(
        "choice"
    ).eq(
        "poll_id", poll_id
    ).eq(
        "user_id", user_id
    ).limit(1).execute()

    if not result.data:
        return None

    return result.data[0]["choice"]


def save_vote(
    poll_id: int,
    user_id: int,
    choice: str,
):
    """
    사용자 투표 저장.

    poll_id + user_id가 UNIQUE이므로
    같은 사람이 다시 투표하면 기존 선택이 변경됨.
    """

    supabase.table("votes").upsert(
        {
            "poll_id": poll_id,
            "user_id": user_id,
            "choice": choice,
        },
        on_conflict="poll_id,user_id",
    ).execute()


def get_count(
    poll_id: int,
    choice: str,
) -> int:
    """특정 선택의 투표 수"""

    result = supabase.table("votes").select(
        "user_id",
        count="exact",
        head=True,
    ).eq(
        "poll_id", poll_id
    ).eq(
        "choice", choice
    ).execute()

    return result.count or 0


def get_counts(poll_id: int) -> dict:
    """전체 투표 수"""

    return {
        "home": get_count(poll_id, "home"),
        "draw": get_count(poll_id, "draw"),
        "away": get_count(poll_id, "away"),
    }


# =========================================================
# 버튼 생성
# =========================================================

def make_keyboard(
    poll_id: int,
    counts: dict,
) -> InlineKeyboardMarkup:

    home = InlineKeyboardButton(
        text=f"🏠 홈 승  {counts['home']}",
        callback_data=f"vote:{poll_id}:home",
        style=CHOICES["home"]["style"],
    )

    draw = InlineKeyboardButton(
        text=f"🤝 무승부  {counts['draw']}",
        callback_data=f"vote:{poll_id}:draw",
        style=CHOICES["draw"]["style"],
    )

    away = InlineKeyboardButton(
        text=f"✈️ 원정 승  {counts['away']}",
        callback_data=f"vote:{poll_id}:away",
        style=CHOICES["away"]["style"],
    )

    return InlineKeyboardMarkup([
        [home, draw, away]
    ])


# =========================================================
# 투표 메시지
# =========================================================

def make_poll_text(counts: dict) -> str:

    total = (
        counts["home"]
        + counts["draw"]
        + counts["away"]
    )

    return (
        "<b>⚽ LET'S BET</b>\n\n"
        "👇 아래 버튼으로 베팅에 참여하세요.\n\n"
        f"👥 현재 참여 <b>{total}명</b>"
    )


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "안녕하세요!\n\n"
        "단체방에서 /bet 명령어를 사용하면 "
        "베팅 투표를 만들 수 있습니다."
    )


# =========================================================
# /bet
# =========================================================

async def bet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    # 단체방에서만 사용
    if chat.type not in ["group", "supergroup"]:
        await message.reply_text(
            "❌ /bet은 단체방에서 사용하는 명령어입니다."
        )
        return

    try:
        # DB에 투표 생성
        poll_id = create_poll(chat.id)

        # 초기 숫자
        counts = {
            "home": 0,
            "draw": 0,
            "away": 0,
        }

        # Telegram 메시지 전송
        sent = await message.reply_text(
            make_poll_text(counts),
            parse_mode="HTML",
            reply_markup=make_keyboard(
                poll_id,
                counts,
            ),
        )

        # 메시지 ID 저장
        set_poll_message_id(
            poll_id,
            sent.message_id,
        )

        logger.info(
            "새 투표 생성: chat=%s poll=%s",
            chat.id,
            poll_id,
        )

    except Exception as e:
        logger.exception("투표 생성 오류")

        await message.reply_text(
            "❌ 투표 생성 중 오류가 발생했습니다.\n"
            f"{e}"
        )


# =========================================================
# 버튼 클릭
# =========================================================

async def vote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    try:
        await query.answer()

        data = query.data

        # vote:123:home
        parts = data.split(":")

        if len(parts) != 3:
            return

        _, poll_id_text, choice = parts

        poll_id = int(poll_id_text)

        if choice not in CHOICES:
            return

        user = query.from_user

        # 기존 선택 확인
        previous_choice = get_user_vote(
            poll_id,
            user.id,
        )

        # 같은 버튼을 다시 누른 경우
        if previous_choice == choice:

            await query.answer(
                f"이미 '{CHOICES[choice]['name']}'에 투표했습니다.",
                show_alert=False,
            )

        else:

            # 투표 저장 / 선택 변경
            save_vote(
                poll_id,
                user.id,
                choice,
            )

            if previous_choice:
                await query.answer(
                    f"{CHOICES[choice]['name']}으로 변경했습니다.",
                    show_alert=False,
                )
            else:
                await query.answer(
                    f"{CHOICES[choice]['name']}에 투표했습니다.",
                    show_alert=False,
                )

        # 최신 숫자 계산
        counts = get_counts(poll_id)

        # 기존 메시지 업데이트
        await query.edit_message_text(
            text=make_poll_text(counts),
            parse_mode="HTML",
            reply_markup=make_keyboard(
                poll_id,
                counts,
            ),
        )

    except Exception as e:

        logger.exception("투표 처리 오류")

        try:
            await query.answer(
                "❌ 처리 중 오류가 발생했습니다.",
                show_alert=True,
            )
        except Exception:
            pass


# =========================================================
# 에러 처리
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Telegram 오류:",
        exc_info=context.error,
    )


# =========================================================
# 실행
# =========================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("bet", bet)
    )

    application.add_handler(
        CallbackQueryHandler(vote, pattern=r"^vote:")
    )

    application.add_error_handler(
        error_handler
    )

    webhook_url = (
        f"{RENDER_EXTERNAL_URL.rstrip('/')}"
        f"/{WEBHOOK_PATH}"
    )

    logger.info(
        "Webhook 시작: %s",
        webhook_url,
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
