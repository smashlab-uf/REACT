def _likert(sub_item_id, text, low_label, high_label, max_value=7, **extra):
    return {
        'sub_item_id': sub_item_id, 'text': text, 'response_type': 'likert',
        'min_value': 1, 'max_value': max_value,
        'low_label': low_label, 'high_label': high_label, **extra,
    }


def _single(sub_item_id, text, choices, **extra):
    return {'sub_item_id': sub_item_id, 'text': text, 'response_type': 'single_choice', 'choices': choices, **extra}


def _multi(sub_item_id, text, choices, **extra):
    return {'sub_item_id': sub_item_id, 'text': text, 'response_type': 'multi_choice', 'choices': choices, **extra}


def _yes_no(sub_item_id, text, **extra):
    return {'sub_item_id': sub_item_id, 'text': text, 'response_type': 'yes_no', 'choices': ['Yes', 'No'], **extra}


def _number(sub_item_id, text, **extra):
    return {'sub_item_id': sub_item_id, 'text': text, 'response_type': 'number', 'min_value': 0, **extra}


# Sub-item content per REACT_IRB01_StudyTeam_Measures_v2.docx Part 1.
# 'depends_on' gates a sub-item on a prior answer in the same check-in:
#   {'sub_item_id': <parent>, 'equals'|'not_equals': <value>}
EMA_ITEM_BANK = {
    'B1': {
        'title': 'Current emotions',
        'sub_items': [
            _likert('B1_valence', 'How would you describe your current emotional state?', 'Very negative', 'Very positive'),
            _likert('B1_arousal', 'How calm or excited do you feel right now?', 'Very calm', 'Very excited'),
            _likert('B1_fluctuation', 'Over the past hour, how much have your emotions fluctuated?', 'Not at all', 'Extremely'),
            _single('B1_change', 'Compared to about an hour ago, how do you feel?',
                    ['Much worse', 'Somewhat worse', 'About the same', 'Somewhat better', 'Much better']),
            _likert('B1_affect_anxious', 'Anxious or on edge', 'Not at all', 'Extremely', max_value=5, group='B1_affect', group_text='Right now, how much do you feel each of the following?'),
            _likert('B1_affect_angry', 'Angry or frustrated', 'Not at all', 'Extremely', max_value=5, group='B1_affect'),
            _likert('B1_affect_sad', 'Sad or down', 'Not at all', 'Extremely', max_value=5, group='B1_affect'),
            _likert('B1_affect_excited', 'Excited or pumped up', 'Not at all', 'Extremely', max_value=5, group='B1_affect'),
            _likert('B1_affect_happy', 'Happy or content', 'Not at all', 'Extremely', max_value=5, group='B1_affect'),
            _likert('B1_affect_bored', 'Bored', 'Not at all', 'Extremely', max_value=5, group='B1_affect'),
        ],
    },
    'B2': {
        'title': 'Situation',
        'sub_items': [
            _likert('B2_stress', 'How stressed do you feel right now?', 'Not at all', 'Extremely'),
            _single('B2_location', 'Where are you right now?',
                    ['Home or dorm', 'Class or library', 'Dining or restaurant', 'Bar or party', 'Gym or rec center', 'Sporting event or watch party', 'Other']),
            _single('B2_social', 'Are you alone or with others right now?',
                    ['Alone', 'With one other person', 'In a group']),
            _single('B2_notable_event', 'In the past hour, did anything notably good or bad happen?',
                    ['Nothing notable', 'Something good', 'Something bad', 'Both']),
            _multi('B2_event_topic', 'If something happened: what was it mostly about?',
                   ['Sports', 'Academics', 'Social or relationship', 'Something online', 'Family', 'Work or money', 'Other'],
                   depends_on={'sub_item_id': 'B2_notable_event', 'not_equals': 'Nothing notable'}),
        ],
    },
    'B3': {
        'title': 'Game context',
        'sub_items': [
            _single('B3_watching', 'Did you watch or attend a UF sporting event today, or are you watching one now?',
                    ['Yes, football', 'Yes, basketball', 'Yes, another UF sport', 'Yes, a non-UF game I care about', 'No']),
            _likert('B3_importance', 'How important is this game to you personally?', 'Not important at all', 'Extremely important'),
            _single('B3_game_status', 'Right now, how is the game going for your team?',
                    ['Winning comfortably', 'Close game', 'Losing', 'Finished, we won', 'Finished, we lost', 'Not sure']),
            _single('B3_watch_mode', 'How are you watching?',
                    ['At the stadium or arena', 'At a tailgate, watch party, or bar', 'At home with others', 'Alone', 'Not watching live']),
            _single('B3_bet', 'Do you have any money bet on this game, including apps, pools, or bets with friends?',
                    ['Yes', 'No', 'Prefer not to say']),
            _single('B3_sudden_shift', 'Did your emotional state change suddenly during the game? If yes, which shift fits best?',
                    ['Calm to anxious', 'Excited to disappointed', 'Disappointed to hopeful', 'Other', 'No sudden change']),
        ],
    },
    'B4': {
        'title': 'Eating',
        'sub_items': [
            _yes_no('B4_ate', 'Since the last check-in, did you eat anything, a meal or a snack?'),
            _likert('B4_hunger', 'If yes: how physically hungry were you when you started eating?', 'Not at all hungry', 'Extremely hungry',
                    depends_on={'sub_item_id': 'B4_ate', 'equals': 'Yes'}),
            _likert('B4_loss_of_control', 'If yes: while eating, how much did you feel a sense of losing control, finding it hard to stop?', 'Not at all', 'Completely lost control',
                    depends_on={'sub_item_id': 'B4_ate', 'equals': 'Yes'}),
            _multi('B4_motivation', 'If yes: what motivated you to eat?',
                   ['Hunger', 'Boredom', 'Stress', 'Feeling upset', 'Celebration or excitement', 'Social or peer context', 'Habit', 'Other'],
                   depends_on={'sub_item_id': 'B4_ate', 'equals': 'Yes'}),
            _likert('B4_emotion_influence', 'If yes: how much did emotions influence your eating?', 'Not at all', 'A great deal',
                    depends_on={'sub_item_id': 'B4_ate', 'equals': 'Yes'}),
            _likert('B4_regret', 'If yes: do you regret or feel bad about anything you ate?', 'No regret at all', 'Very much regret',
                    depends_on={'sub_item_id': 'B4_ate', 'equals': 'Yes'}),
            _single('B4_skipped_meal', 'Since the last check-in, did you skip a meal you would normally have eaten?',
                    ['No', 'Yes, no appetite', 'Yes, too busy', 'Yes, feeling upset or stressed', 'Yes, other reason']),
            _likert('B4_urge', 'Right now, how strong is your urge to eat?', 'No urge at all', 'Extremely strong urge'),
        ],
    },
    'B5': {
        'title': 'Handling emotions',
        'sub_items': [
            _multi('B5_regulation', 'Since the last check-in, did you try to manage or regulate your emotions in any way? If yes, which did you use?',
                   ['Distraction', 'Delay, waiting it out', 'Reframing, positive thinking', 'Breathing or mindfulness', 'Eating', 'Drinking alcohol',
                    'Posting or venting online', 'Exercise or movement', 'Talking to someone', 'Other', 'Did not try to regulate']),
            _likert('B5_success', 'How successful were you in managing your urges?', 'Not at all successful', 'Very successful'),
            _likert('B5_control', 'Right now, how in control of your behavior do you feel?', 'Not at all in control', 'Completely in control'),
        ],
    },
    'B6': {
        'title': 'Alcohol',
        'sub_items': [
            _single('B6_plan', 'At the first afternoon check-in: do you plan to drink alcohol later today or tonight?',
                    ['No', 'Maybe', 'Yes, a little', 'Yes, a lot'],
                    schedule_condition='first_afternoon_check_in'),
            _yes_no('B6_consumed', 'Have you consumed any alcohol today?'),
            _number('B6_drink_count', 'If yes: about how many standard drinks have you had?',
                    depends_on={'sub_item_id': 'B6_consumed', 'equals': 'Yes'}),
            _multi('B6_reason', 'If yes: what best describes why?',
                   ['Social reasons', 'Celebration', 'Coping with stress or emotions', 'Boredom', 'Habit', 'Peer context', 'Other'],
                   depends_on={'sub_item_id': 'B6_consumed', 'equals': 'Yes'}),
            _single('B6_context', 'If yes: where and with whom?',
                    ['Tailgate or watch party', 'Bar or party', 'Home with others', 'Home alone', 'Other'],
                    depends_on={'sub_item_id': 'B6_consumed', 'equals': 'Yes'}),
            _likert('B6_urge', 'Right now, how strong is your urge to drink alcohol?', 'No urge at all', 'Extremely strong urge'),
            _likert('B6_mood_effect', 'How much do you feel alcohol affected your mood today?', 'Not at all', 'A great deal'),
        ],
    },
    'B7': {
        'title': 'Online activity',
        'sub_items': [
            _yes_no('B7_posted', 'Did you post or comment online in the past few hours?'),
            _yes_no('B7_saw_upsetting', 'Did you see anything online recently that made you feel angry or upset?'),
            _likert('B7_urge', 'Right now, how strong is your urge to post or reply about something that upset you?', 'No urge at all', 'Extremely strong urge'),
            _single('B7_self_stopped', 'In the heat of the moment, did you stop yourself from posting or sending something?',
                    ['Yes', 'No', 'Did not feel the urge']),
            _likert('B7_charged', 'Looking back, how emotionally charged was your own online activity?', 'Not at all', 'Extremely'),
            _likert('B7_regret', 'Do you regret anything about your own online activity in the past few hours?', 'No regret', 'Very much regret'),
        ],
    },
    'B8': {
        'title': 'Morning',
        'sub_items': [
            _likert('B8_sleep', 'How well did you sleep last night?', 'Very poorly', 'Very well'),
            _single('B8_drank_more_than_planned', 'Thinking about yesterday evening: did you drink alcohol? If yes, did you end up drinking more than you had planned?',
                    ['Did not drink', 'Drank about what I planned', 'Somewhat more than planned', 'Much more than planned']),
            _multi('B8_regrets', 'Looking back at yesterday, do you regret anything you posted, ate, or drank?',
                   ['Something I posted or sent', 'Something I ate', 'Something I drank', 'No regrets']),
            _multi('B8_academic_pressure', 'Are you currently under academic pressure? If yes, what kind?',
                   ['Exams this week', 'Deadlines or projects', 'Presentations', 'Other', 'Not under pressure']),
            _yes_no('B8_caffeine', 'Did you consume caffeine or energy drinks today?'),
        ],
    },
    # Per REACT_IRB01_StudyTeam_Measures_v2.docx Part 2 — shown right after
    # any delivered prompt, coping or active-control. Dismissing without
    # answering is recorded as missing, not as a negative answer (see the
    # 'dismissed' EMA status).
    'C0': {
        'title': 'Quick rating',
        'sub_items': [
            _single('C0_helpful', 'Was this message helpful right now?', ['Yes', 'Somewhat', 'No']),
            _single('C0_behavior_change', 'Did it change what you did next?',
                    ['I paused or waited', 'I changed what I was going to do', 'Nothing different', 'It did not fit the moment']),
        ],
    },
}

EMA_SUB_ITEM_INDEX = {
    sub_item['sub_item_id']: {'item_id': item_id, **sub_item}
    for item_id, item in EMA_ITEM_BANK.items()
    for sub_item in item['sub_items']
}

POST_PROMPT_ITEM_IDS = ['B1', 'B2', 'B4', 'B5', 'B6', 'B7']
ROTATING_ITEM_IDS = ['B4', 'B5', 'B6', 'B7']
PROMPT_FEEDBACK_ITEM_IDS = ['C0']
EVENING_CHECK_IN_HOUR = 20
AFTERNOON_START_HOUR = 12

# Two distinct caps, confirmed by Dr. Chang 2026-08-21 — do not merge them.
# SCHEDULED governs the B1-B8 rotation check-ins spread through the day.
# POST_PROMPT governs the JITAI-triggered outcome-window check-ins (both the
# 'post_prompt' type and the inserted 'extra_check_in' type share this cap).
SCHEDULED_CHECK_IN_DAILY_CAP = 6
POST_PROMPT_CHECK_IN_DAILY_CAP = 4


def ema_items(item_ids):
    return [
        {'item_id': item_id, 'title': EMA_ITEM_BANK[item_id]['title'], 'sub_items': EMA_ITEM_BANK[item_id]['sub_items']}
        for item_id in item_ids
    ]
