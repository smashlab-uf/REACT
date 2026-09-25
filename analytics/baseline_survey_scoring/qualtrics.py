"""Map a Qualtrics baseline-survey export onto definitions.py column names.

Qualtrics exports carry either the item text or a QID as the column header,
never definitions.py's short names. This module builds one lookup from every
known item's text (and from definitions.py's own names, so an export that
already uses them needs no text matching) and uses it to rename an export's
columns and coerce its responses to numbers.
"""

from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import definitions as D


MISSING_TOKENS = {"", "-99", "-999", "na", "n/a", "nan", "none", "null",
                  "prefer not to say", "prefer not to answer", "."}


def normalize(text) -> str:
    """Casefold, strip accents and punctuation, drop list numbering, collapse space."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Curly quotes and dashes vary between Word, Qualtrics and this notebook.
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("–", "-").replace("—", "-"))
    text = text.strip().lower()
    text = re.sub(r"^\s*\d+[.)]\s*", "", text)          # leading "1." or "1)"
    text = re.sub(r"\s*\(r\)\s*$", "", text)            # trailing reverse marker
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Section 1 — response option tables.

SUPPS_OPTIONS = {
    "strongly agree": 1, "somewhat agree": 2,
    "somewhat disagree": 3, "strongly disagree": 4,
}

DERS_OPTIONS = {
    "almost never (0-10%)": 1, "almost never": 1,
    "sometimes (11-35%)": 2, "sometimes": 2,
    "about half the time (36-65%)": 3, "about half the time": 3,
    "most of the time (66-90%)": 4, "most of the time": 4,
    "almost always (91-100%)": 5, "almost always": 5,
}

ERQ_OPTIONS = {
    "strongly disagree": 1, "disagree": 2, "slightly disagree": 3,
    "neutral": 4, "slightly agree": 5, "agree": 6, "strongly agree": 7,
}

BAQ_OPTIONS = {
    "extremely uncharacteristic of me": 1, "uncharacteristic of me": 2,
    "slightly uncharacteristic of me": 3, "neither": 4,
    "slightly characteristic of me": 5, "characteristic of me": 6,
    "extremely characteristic of me": 7,
}

BSMAS_OPTIONS = {
    "very rarely": 1, "rarely": 2, "sometimes": 3, "often": 4, "very often": 5,
}

# PSS is the one Likert here that starts at 0, not 1.
PSS_OPTIONS = {
    "never": 0, "almost never": 1, "sometimes": 2,
    "fairly often": 3, "very often": 4,
}

# PROMIS item 1 has its own wording; items 2 to 4 share the other. Both are
# stored in the direction the participant saw. Items 1 and 2 are reversed later,
# exactly once. The docx prints these two already flipped ("5 = Very poor"), and
# reversing those printed values would flip them back.
PROMIS_QUALITY_OPTIONS = {
    "very poor": 1, "poor": 2, "fair": 3, "good": 4, "very good": 5,
}
PROMIS_AMOUNT_OPTIONS = {
    "not at all": 1, "a little bit": 2, "somewhat": 3,
    "quite a bit": 4, "very much": 5,
}

# TFEQ-R18 uses four different four-category formats, each coded 1 to 4 in
# increasing order of the behaviour being measured.
TFEQ_TRUE_FALSE_OPTIONS = {
    "definitely false": 1, "mostly false": 2,
    "mostly true": 3, "definitely true": 4,
}
TFEQ_STOCKING_OPTIONS = {
    "almost never": 1, "seldom": 2, "usually": 3, "almost always": 4,
}
TFEQ_EAT_LESS_OPTIONS = {
    "unlikely": 1, "slightly likely": 2,
    "moderately likely": 3, "very likely": 4,
}
TFEQ_HUNGRY_OPTIONS = {
    "only at mealtimes": 1, "sometimes between meals": 2,
    "often between meals": 3, "almost always": 4,
}
TFEQ_BINGE_OPTIONS = {
    "never": 1, "rarely": 2, "sometimes": 3, "at least once a week": 4,
}

# MAIA is the one scale that starts at 0 and runs to 5, six points not five.
MAIA_OPTIONS = {
    "never": 0, "1": 1, "2": 2, "3": 3, "4": 4, "always": 5,
}

AUDIT_C_FREQUENCY_OPTIONS = {
    "never": 0, "monthly or less": 1, "2 to 4 times a month": 2,
    "2 to 3 times a week": 3, "4 or more times a week": 4,
}
AUDIT_C_QUANTITY_OPTIONS = {
    "1 or 2": 0, "3 or 4": 1, "5 or 6": 2,
    "7 to 9": 3, "7, 8, or 9": 3, "10 or more": 4,
}
AUDIT_C_BINGE_OPTIONS = {
    "never": 0, "less than monthly": 1, "monthly": 2,
    "weekly": 3, "daily or almost daily": 4,
}

# The docx gives five points. The superseded xlsx gives three. Kuntsche and
# Kuntsche (2009) use five, so the docx is right.
DMQ_OPTIONS = {
    "almost never/never": 1, "almost never or never": 1, "never": 1,
    "some of the time": 2, "half the time": 3,
    "most of the time": 4,
    "almost always/always": 5, "almost always or always": 5, "always": 5,
}

YES_NO_OPTIONS = {"yes": 1, "no": 0}

PGSI_OPTIONS = {
    "never": 0, "sometimes": 1, "most of the time": 2, "almost always": 3,
}

# Screened, not summed: either item Often or Sometimes true is a positive.
HUNGER_OPTIONS = {
    "never true": 0, "sometimes true": 1, "often true": 2,
}

ASRS_OPTIONS = {
    "never": 0, "rarely": 1, "sometimes": 2, "often": 3, "very often": 4,
}

UCLA_OPTIONS = {
    "hardly ever": 1, "some of the time": 2, "often": 3,
}

EDS_OPTIONS = {
    "never": 1, "less than once a year": 2, "a few times a year": 3,
    "a few times a month": 4, "at least once a week": 5,
}

# rMEQ is five items with five different maps, and the last runs 6/4/2/0.
RMEQ_RISE_OPTIONS = {
    "5 a.m. - 6:30 a.m.": 5, "6:30 a.m. - 7:45 a.m.": 4,
    "7:45 a.m. - 9:45 a.m.": 3, "9:45 a.m. - 11 a.m.": 2,
    "11 a.m. - 12 noon": 1,
}
RMEQ_TIRED_OPTIONS = {
    "very tired": 1, "fairly tired": 2,
    "fairly refreshed": 3, "very refreshed": 4,
}
RMEQ_EVENING_OPTIONS = {
    "8 p.m. - 9 p.m.": 5, "9 p.m. - 10:15 p.m.": 4,
    "10:15 p.m. - 12:30 a.m.": 3, "12:30 a.m. - 1:45 a.m.": 2,
    "1:45 a.m. - 3 a.m.": 1,
}
RMEQ_PEAK_OPTIONS = {
    "10 p.m. - 5 a.m.": 1, "5 p.m. - 10 p.m.": 2, "10 a.m. - 5 p.m.": 3,
    "8 a.m. - 10 a.m.": 4, "5 a.m. - 8 a.m.": 5,
}
RMEQ_TYPE_OPTIONS = {
    "definitely a morning type": 6,
    "rather more a morning than evening type": 4,
    "rather more an evening than morning type": 2,
    "definitely an evening type": 0,
}

PHQ9_OPTIONS = {
    "not at all": 0, "several days": 1,
    "more than half the days": 2, "nearly every day": 3,
}


# Section 2 — the master item table.

ITEM_TEXT: Dict[str, List[str]] = {}
ITEM_OPTIONS: Dict[str, list] = {}

ITEM_TEXT["ssis"] = [
    "How important to YOU is it that your favorite sports team wins?",
    "How strongly do YOU see YOURSELF as a fan of your favorite sports team?",
    "How strongly do your FRIENDS see YOU as a fan of your favorite sports team?",
    "During the season, how closely do you follow your favorite sports team via ANY of the "
    "following: a) in person or on television, b) on the radio, or c) television news or a newspaper?",
    "How important is being a fan of your favorite sports team to YOU?",
    "How much do YOU dislike your favorite sports team's greatest rivals?",
    "How often do YOU display your favorite sports team's name or insignia at your place of work, "
    "where you live, or on your clothing?",
]

ITEM_TEXT["supps"] = [
    "When I feel bad, I will often do things I later regret in order to make myself feel better now.",
    "Sometimes when I feel bad, I can't seem to stop what I am doing even though it is making me feel worse.",
    "When I am upset I often act without thinking.",
    "When I feel rejected, I will often say things that I later regret.",
    "I generally like to see things through to the end.",
    "Unfinished tasks really bother me.",
    "Once I get going on something I hate to stop.",
    "I finish what I start.",
    "My thinking is usually careful and purposeful.",
    "I like to stop and think things over before I do them.",
    "I tend to value and follow a rational, \"sensible\" approach to things.",
    "I usually think carefully before doing anything.",
    "I quite enjoy taking risks.",
    "I welcome new and exciting experiences and sensations, even if they are a little frightening "
    "and unconventional.",
    "I would like to learn to fly an airplane.",
    "I would enjoy the sensation of skiing very fast down a high mountain slope.",
    "When I am in great mood, I tend to get into situations that could cause me problems.",
    "I tend to lose control when I am in a great mood.",
    "Others are shocked or worried about the things I do when I am feeling very excited.",
    "I tend to act without thinking when I am really excited.",
]

ITEM_TEXT["ders"] = [
    "I have difficulty making sense out of my feelings.",
    "I am confused about how I feel.",
    "When I am upset, I have difficulty getting work done.",
    "When I am upset, I have difficulty focusing on other things.",
    "When I am upset, I have difficulty thinking about anything else.",
    "When I am upset, I become out of control.",
    "When I am upset, I feel out of control.",
    "When I am upset, I have difficulty controlling my behaviors.",
    "When I am upset, I believe that I will remain that way for a long time.",
    "When I am upset, I believe that I'll end up feeling very depressed.",
    "When I am upset, I believe that there is nothing I can do to make myself feel better",
    "When I am upset, I start to feel very bad about myself.",
    "When I am upset, my emotions feel overwhelming.",
    "When I am upset, I feel ashamed with myself for feeling that way.",
    "When I am upset, I feel like I am weak.",
    "When I am upset, I become irritated with myself for feeling that way.",
]

ITEM_TEXT["erq"] = [
    "I control my emotions by changing the way I think about the situation I'm in.",
    "When I want to feel less negative emotion, I change the way I'm thinking about the situation.",
    "When I want to feel more positive emotion, I change the way I'm thinking about the situation.",
    "When I want to feel more positive emotion (such as joy or amusement), I change what I'm thinking about.",
    "When I want to feel less negative emotion (such as sadness or anger), I change what I'm thinking about.",
    "When I'm faced with a stressful situation, I make myself think about it in a way that helps me stay calm.",
    "I control my emotions by not expressing them.",
    "When I am feeling negative emotions, I make sure not to express them.",
    "I keep my emotions to myself.",
    "When I am feeling positive emotions, I am careful not to express them.",
]

ITEM_TEXT["baq"] = [
    "Given enough provocation, I may hit another person.",
    "If I have to resort to violence to protect my rights, I will.",
    "There are people who pushed me so far that we came to blows.",
    "I am an even-tempered person.",
    "Sometimes I fly off the handle for no good reason.",
    "I have trouble controlling my temper.",
    "I tell my friends openly when I disagree with them.",
    "When people annoy me, I may tell them what I think of them.",
    "My friends say that I'm somewhat argumentative.",
    "Other people always seem to get the breaks.",
    "I sometimes feel that people are laughing at me behind my back.",
    "When people are especially nice, I wonder what they want.",
]

ITEM_TEXT["bsmas"] = [
    "Spent a lot of time thinking about social media or planned use of social media?",
    "Felt an urge to use social media more and more?",
    "Used social media in order to forget about personal problems?",
    "Tried to cut down on the use of social media without success?",
    "Become restless or troubled if you have been prohibited from using social media?",
    "Used social media so much that it has had a negative impact on your job/studies?",
]

ITEM_TEXT["pss"] = [
    "In the last month, how often have you been upset because of something that happened unexpectedly?",
    "In the last month, how often have you felt that you were unable to control the important things in your life?",
    "In the last month, how often have you felt nervous and \"stressed\"?",
    "In the last month, how often have you felt confident about your ability to handle your personal problems?",
    "In the last month, how often have you felt that things were going your way?",
    "In the last month, how often have you found that you could not cope with all the things that you had to do?",
    "In the last month, how often have you been able to control irritations in your life?",
    "In the last month, how often have you felt that you were on top of things?",
    "In the last month, how often have you been angered because of things that were outside of your control?",
    "In the last month, how often have you felt difficulties were piling up so high that you could not overcome them?",
]

ITEM_TEXT["promis_sleep"] = [
    "My sleep quality was",
    "My sleep was refreshing.",
    "I had a problem with my sleep.",
    "I had difficulty falling asleep.",
]
ITEM_OPTIONS["promis_sleep"] = [
    PROMIS_QUALITY_OPTIONS, PROMIS_AMOUNT_OPTIONS,
    PROMIS_AMOUNT_OPTIONS, PROMIS_AMOUNT_OPTIONS,
]

ITEM_TEXT["tfeq"] = [
    "I deliberately take small helpings as a means of controlling my weight.",
    "I consciously hold back at meals in order not to gain weight.",
    "I do not eat some foods because they make me fat.",
    "How frequently do you avoid 'stocking up' on tempting foods?",
    "How likely are you to consciously eat less than you want?",
    "On a scale of 1 to 8, where 1 means no restraint in eating (eating whatever you want, whenever "
    "you want it) and 8 means total restraint (constantly limiting food intake and never 'giving "
    "in'), what number would you give yourself?",
    "When I smell a sizzling steak or a juicy piece of meat, I find it very difficult to keep from "
    "eating, even if I have just finished a meal.",
    "Sometimes when I start eating, I just can't seem to stop.",
    "Being with someone who is eating often makes me hungry enough to eat also.",
    "When I see a real delicacy, I often get so hungry that I have to eat right away.",
    "I get so hungry that my stomach often seems like a bottomless pit.",
    "I am always hungry so it is hard for me to stop eating before I finish the food on my plate.",
    "I am always hungry enough to eat at any time.",
    "How often do you feel hungry?",
    "Do you go on eating binges though you are not hungry?",
    "When I feel anxious, I find myself eating.",
    "When I feel blue, I often overeat.",
    "When I feel lonely, I console myself by eating.",
]
ITEM_OPTIONS["tfeq"] = (
    [TFEQ_TRUE_FALSE_OPTIONS] * 3
    + [TFEQ_STOCKING_OPTIONS, TFEQ_EAT_LESS_OPTIONS, None]
    + [TFEQ_TRUE_FALSE_OPTIONS] * 7
    + [TFEQ_HUNGRY_OPTIONS, TFEQ_BINGE_OPTIONS]
    + [TFEQ_TRUE_FALSE_OPTIONS] * 3
)

ITEM_TEXT["maia"] = [
    "When I am tense I notice where the tension is located in my body.",
    "I notice when I am uncomfortable in my body.",
    "I notice where in my body I am comfortable.",
    "I notice changes in my breathing, such as whether it slows down or speeds up.",
    "I listen for information from my body about my emotional state.",
    "When I am upset, I take time to explore how my body feels.",
    "I listen to my body to inform me about what to do.",
]

ITEM_TEXT["audit_c"] = [
    "How often did you have a drink containing alcohol in the past year?",
    "How many standard drinks did you have on a typical day when you were drinking in the past year?",
    "How often did you have six or more drinks on one occasion in the past year?",
]
ITEM_OPTIONS["audit_c"] = [
    AUDIT_C_FREQUENCY_OPTIONS, AUDIT_C_QUANTITY_OPTIONS, AUDIT_C_BINGE_OPTIONS,
]

ITEM_TEXT["dmq"] = [
    "In the last 12 months, how often did you drink because you like the feeling?",
    "In the last 12 months, how often did you drink to get high?",
    "In the last 12 months, how often did you drink because it's fun?",
    "In the last 12 months, how often did you drink because it helps you enjoy a party?",
    "In the last 12 months, how often did you drink because it makes social gatherings more fun?",
    "In the last 12 months, how often did you drink because it improves parties and celebrations?",
    "In the last 12 months, how often did you drink to fit in with a group you like?",
    "In the last 12 months, how often did you drink to be liked?",
    "In the last 12 months, how often did you drink so you won't feel left out?",
    "In the last 12 months, how often did you drink because it helps you when you feel depressed or nervous?",
    "In the last 12 months, how often did you drink to cheer up when you're in a bad mood?",
    "In the last 12 months, how often did you drink to forget about your problems?",
]

ITEM_TEXT["byaacq"] = [
    "While drinking, I have said or done embarrassing things.",
    "I have had a hangover (headache, sick stomach) the morning after I had been drinking.",
    "I have felt very sick to my stomach or thrown up after drinking.",
    "I often have ended up drinking on nights when I had planned not to drink.",
    "I have taken foolish risks when I have been drinking.",
    "I have passed out from drinking.",
    "I have found that I needed larger amounts of alcohol to feel any effect, or that I could no "
    "longer get high or drunk on the amount that used to get me high or drunk.",
    "When drinking, I have done impulsive things I regretted later.",
    "I've not been able to remember large stretches of time while drinking heavily.",
    "I have driven a car when I knew I had too much to drink to drive safely.",
    "I have not gone to work or missed classes at school because of drinking, a hangover, or "
    "illness caused by drinking.",
    "My drinking has gotten me into sexual situations I later regretted.",
    "I have often found it difficult to limit how much I drink.",
    "I have become very rude, obnoxious, or insulting after drinking.",
    "I have woken up in an unexpected place after heavy drinking.",
    "I have felt badly about myself because of my drinking.",
    "I have had less energy or felt tired because of my drinking.",
    "The quality of my work or school work has suffered because of my drinking.",
    "I have spent too much time drinking.",
    "I have neglected my obligations to family, work, or school because of drinking.",
    "My drinking has created problems between myself and my boyfriend/girlfriend/spouse, parents, "
    "or other near relatives.",
    "I have been overweight because of drinking.",
    "My physical appearance has been harmed by my drinking.",
    "I have felt like I needed a drink after I'd gotten up (that is, before breakfast).",
]

ITEM_TEXT["pgsi"] = [
    "Have you bet more than you could really afford to lose?",
    "Still thinking about the last 12 months, have you needed to gamble with larger amounts of "
    "money to get the same feeling of excitement?",
    "When you gambled, did you go back another day to try to win back the money you lost?",
    "Have you borrowed money or sold anything to get money to gamble?",
    "Have you felt that you might have a problem with gambling?",
    "Has gambling caused you any health problems, including stress or anxiety?",
    "Have people criticized your betting or told you that you had a gambling problem, regardless of "
    "whether or not you thought it was true?",
    "Has your gambling caused any financial problems for you or your household?",
    "Have you felt guilty about the way you gamble or what happens when you gamble?",
]

ITEM_TEXT["hunger_vital_sign"] = [
    "Within the past 12 months we worried whether our food would run out before we got money to buy more.",
    "Within the past 12 months the food we bought just didn't last and we didn't have money to get more.",
]

ITEM_TEXT["asrs"] = [
    "How often do you have trouble wrapping up the final details of a project, once the challenging "
    "parts have been done?",
    "How often do you have difficulty getting things in order when you have to do a task that "
    "requires organization?",
    "How often do you have problems remembering appointments or obligations?",
    "When you have a task that requires a lot of thought, how often do you avoid or delay getting started?",
    "How often do you fidget or squirm with your hands or feet when you have to sit down for a long time?",
    "How often do you feel overly active and compelled to do things, like you were driven by a motor?",
]

ITEM_TEXT["ucla3"] = [
    "First, how often do you feel that you lack companionship?",
    "How often do you feel left out?",
    "How often do you feel isolated from others?",
]

ITEM_TEXT["eds"] = [
    "Treated with less courtesy/respect than other people",
    "Poorer service than others at restaurant/store",
    "People act afraid of you",
    "Threatened/harassed",
    "People act as if they think you are not smart",
]

ITEM_TEXT["rmeq"] = [
    "Considering only your own \"feeling best\" rhythms, at what time would you get up if you were "
    "entirely free to plan your day?",
    "During the first half hour after awakening in the morning, how tired do you feel?",
    "At what time in the evening do you feel tired and in need of sleep?",
    "At what time of the day do you think that you reach your \"feeling best\" peak?",
    "One hears about \"morning\" and \"evening\" types of people. Which one of these types do you "
    "consider yourself to be?",
]
ITEM_OPTIONS["rmeq"] = [
    RMEQ_RISE_OPTIONS, RMEQ_TIRED_OPTIONS, RMEQ_EVENING_OPTIONS,
    RMEQ_PEAK_OPTIONS, RMEQ_TYPE_OPTIONS,
]

ITEM_TEXT["phq9"] = [
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself, or that you are a failure, or have let yourself or your family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed; or the opposite, being so "
    "fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead or of hurting yourself in some way",
]

ITEM_TEXT["scoff"] = [
    "Do you make yourself Sick because you feel uncomfortably full?",
    "Do you worry you have lost Control over how much you eat?",
    "Have you recently lost more than Fifteen pounds in a 3 month period?",
    "Do you believe yourself to be Fat when others say you are too thin?",
    "Would you say that Food dominates your life?",
]

ITEM_TEXT["ace"] = [
    "Did a parent or other adult in the household often... Swear at you, insult you, put you down, "
    "or humiliate you? or Act in a way that made you afraid that you might be physically hurt?",
    "Did a parent or other adult in the household often... Push, grab, slap, or throw something at "
    "you? or Ever hit you so hard that you had marks or were injured?",
    "Did you experience unwanted sexual contact (such as fondling or oral/anal/vaginal "
    "intercourse/penetration)?",
    "Did you often feel that... No one in your family loved you or thought you were important or "
    "special? or Your family didn't look out for each other, feel close to each other, or support "
    "each other?",
    "Did you often feel that... You didn't have enough to eat, had to wear dirty clothes, and had "
    "no one to protect you? or Your parents were too drunk or high to take care of you or take you "
    "to the doctor if you needed it?",
    "Were your parents ever separated or divorced?",
    "Did your parents or adults in your home ever hit, punch, beat, or threaten to harm each other?",
    "Did you live with anyone who was a problem drinker or alcoholic or who used street drugs?",
    "Was a household member depressed or mentally ill or did a household member attempt suicide?",
    "Did a household member go to prison?",
]

ITEM_TEXT["macarthur"] = [
    "Where would you place yourself on this ladder relative to other people in their communities?",
    "Where would you place yourself on this ladder relative to other people in the United States?",
]

ITEM_TEXT["digital_habits"] = [
    "On a typical day, about how much time do you spend on social media and messaging apps?",
    "How often do you post or reply when you are feeling upset or angry?",
    "How often do you later regret something you posted or sent?",
]

# Column tuples from definitions.py, in the same order as the text above.
ITEM_COLUMNS = {
    "ssis": D.SSIS,
    "supps": D.SUPPS,
    "ders": D.DERS,
    "erq": D.ERQ,
    "baq": D.BAQ,
    "bsmas": D.BSMAS,
    "pss": D.PSS,
    "promis_sleep": D.PROMIS_SLEEP,
    "tfeq": D.TFEQ,
    "maia": D.MAIA_TIER1,
    "audit_c": D.AUDIT_C,
    "dmq": D.DMQ_R_SF,
    "byaacq": D.BYAACQ,
    "pgsi": D.PGSI,
    "hunger_vital_sign": D.HUNGER_VITAL_SIGN,
    "asrs": D.ASRS,
    "ucla3": D.UCLA3,
    "eds": D.EDS,
    "rmeq": D.RMEQ,
    "phq9": D.PHQ9,
    "scoff": D.SCOFF,
    "ace": D.ACE,
    "macarthur": D.MACARTHUR,
    "digital_habits": D.DIGITAL_HABITS,
}

# One option table per instrument, unless ITEM_OPTIONS already gave a per-item
# list because the instrument mixes formats.
INSTRUMENT_OPTIONS = {
    "ssis": None,
    "supps": SUPPS_OPTIONS,
    "ders": DERS_OPTIONS,
    "erq": ERQ_OPTIONS,
    "baq": BAQ_OPTIONS,
    "bsmas": BSMAS_OPTIONS,
    "pss": PSS_OPTIONS,
    "maia": MAIA_OPTIONS,
    "dmq": DMQ_OPTIONS,
    "byaacq": YES_NO_OPTIONS,
    "pgsi": PGSI_OPTIONS,
    "hunger_vital_sign": HUNGER_OPTIONS,
    "asrs": ASRS_OPTIONS,
    "ucla3": UCLA_OPTIONS,
    "eds": EDS_OPTIONS,
    "phq9": PHQ9_OPTIONS,
    "scoff": YES_NO_OPTIONS,
    "ace": YES_NO_OPTIONS,
    "macarthur": None,
    "digital_habits": None,
}


def build_item_table() -> pd.DataFrame:
    """Flatten the per-instrument lists into one row per item."""
    rows = []
    for instrument, columns in ITEM_COLUMNS.items():
        texts = ITEM_TEXT[instrument]
        if len(texts) != len(columns):
            raise ValueError(
                f"{instrument}: {len(texts)} item texts but {len(columns)} columns in "
                "definitions.py. These are paired positionally and must match."
            )
        per_item = ITEM_OPTIONS.get(instrument)
        for index, (text, column) in enumerate(zip(texts, columns)):
            options = per_item[index] if per_item else INSTRUMENT_OPTIONS.get(instrument)
            rows.append({
                "instrument": instrument,
                "position": index + 1,
                "column": column,
                "text": text,
                "options": options,
            })
    return pd.DataFrame(rows)


ITEM_TABLE = build_item_table()
_scored = set(D.ALL_SCORING_COLUMNS)
_mapped = set(ITEM_TABLE["column"])
_unmapped = sorted(_scored - _mapped)
if _unmapped:
    raise ValueError(f"scoring columns with no item text: {_unmapped}")


# Section 3 — the loader.

NORMALIZED_ITEM_LOOKUP: Dict[str, str] = {}
for _row in ITEM_TABLE.itertuples():
    _key = normalize(_row.text)
    if _key in NORMALIZED_ITEM_LOOKUP and NORMALIZED_ITEM_LOOKUP[_key] != _row.column:
        raise ValueError(
            f"two items normalize to the same text: {_row.column} and "
            f"{NORMALIZED_ITEM_LOOKUP[_key]}. Text matching cannot tell them apart."
        )
    NORMALIZED_ITEM_LOOKUP[_key] = _row.column

# definitions.py names and the preserved columns are accepted verbatim too, so an
# export that already uses short variable names needs no text matching at all.
for _column in tuple(D.ALL_SCORING_COLUMNS) + tuple(D.ALL_PRESERVED_COLUMNS) + (D.PARTICIPANT_ID,):
    NORMALIZED_ITEM_LOOKUP.setdefault(normalize(_column), _column)


def resolve_columns(
    headers: List[str],
    overrides: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Header -> definitions.py column name, plus the headers that did not place.

    overrides maps an export's exact header text to a definitions.py column
    name, for headers text matching cannot place (e.g. a Qualtrics QID export).
    """
    overrides = overrides or {}
    resolved, unmatched = {}, []
    for header in headers:
        if header in overrides:
            resolved[header] = overrides[header]
            continue
        target = NORMALIZED_ITEM_LOOKUP.get(normalize(header))
        if target is None:
            unmatched.append(header)
        else:
            resolved[header] = target
    return resolved, unmatched


def to_numeric(series: pd.Series, options: Optional[dict]) -> Tuple[pd.Series, List[str]]:
    """Coerce one item to numbers, through its option table when it is text.

    Values already numeric are kept. Missing tokens become NA. A label that is
    not in the option table becomes NA and is counted by the caller, never
    guessed at.
    """
    out, unknown = [], []
    for value in series:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            out.append(np.nan)
            continue
        if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
            out.append(float(value))
            continue
        text = str(value).strip()
        if normalize(text) in MISSING_TOKENS or text == "":
            out.append(np.nan)
            continue
        if options:
            key = normalize(text)
            lookup = {normalize(label): score for label, score in options.items()}
            if key in lookup:
                out.append(float(lookup[key]))
                continue
        try:
            out.append(float(text))
        except ValueError:
            out.append(np.nan)
            unknown.append(text)
    return pd.Series(out, index=series.index, dtype="float64"), unknown


def read_export(source, overrides: Optional[Dict[str, str]] = None) -> Tuple[pd.DataFrame, dict]:
    """Read an export from a path or an open binary file and return
    (frame, diagnostics).

    The frame has definitions.py column names and numbers. diagnostics reports what
    could not be placed: absent_columns (scoring items missing from the export),
    unmatched_headers (export columns that map to no item), and unknown_labels
    (response text not in an item's option table, read as NA). A file-like source
    is read as CSV, in memory, and never written anywhere.
    """
    if hasattr(source, "read"):
        raw = pd.read_csv(source, dtype=object, keep_default_na=False, encoding="utf-8-sig")
    else:
        path = Path(source)
        if path.suffix.lower() in {".xlsx", ".xls"}:
            raw = pd.read_excel(path, dtype=object)
        else:
            raw = pd.read_csv(path, dtype=object, keep_default_na=False, encoding="utf-8-sig")

    # Qualtrics writes two metadata rows under the header. The first cell of the
    # second one is the ImportId JSON blob, which is how it is recognised.
    if len(raw) >= 2 and raw.iloc[1].astype(str).str.contains("ImportId").any():
        raw = raw.iloc[2:].reset_index(drop=True)

    resolved, unmatched = resolve_columns(list(raw.columns), overrides)
    frame = raw.rename(columns=resolved)
    frame = frame.loc[:, ~frame.columns.duplicated()]

    options_by_column = dict(zip(ITEM_TABLE["column"], ITEM_TABLE["options"]))
    unknown_labels = {}
    for column in D.ALL_SCORING_COLUMNS:
        if column not in frame.columns:
            continue
        converted, unknown = to_numeric(frame[column], options_by_column.get(column))
        frame[column] = converted
        if unknown:
            unknown_labels[column] = sorted(set(unknown))

    diagnostics = {
        "absent_columns": [c for c in D.ALL_SCORING_COLUMNS if c not in frame.columns],
        "unmatched_headers": list(unmatched),
        "unknown_labels": unknown_labels,
    }
    return frame, diagnostics


def load_export(path, overrides: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """Read an export and return it with definitions.py column names and numbers."""
    frame, diagnostics = read_export(path, overrides)
    absent = diagnostics["absent_columns"]
    unmatched = diagnostics["unmatched_headers"]
    unknown_labels = diagnostics["unknown_labels"]
    present = [c for c in D.ALL_SCORING_COLUMNS if c in frame.columns]

    print(f"loaded {len(frame)} rows from {Path(path).name}")
    print(f"  scoring items found: {len(present)} of {len(D.ALL_SCORING_COLUMNS)}")
    if absent:
        print(f"  !! {len(absent)} scoring items NOT found; every subscale using one will be NA:")
        for column in absent:
            print(f"       {column}")
    if unmatched:
        print(f"  !! {len(unmatched)} export columns did not map to any item:")
        for header in unmatched:
            print(f"       {header!r}")
        print("     Pass the real item as an override to load_export(path, overrides=...).")
    if unknown_labels:
        print("  !! response labels not in the option tables, read as NA:")
        for column, labels in unknown_labels.items():
            print(f"       {column}: {labels}")
    return frame
