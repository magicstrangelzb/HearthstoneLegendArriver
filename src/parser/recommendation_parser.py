"""Deterministic recommendation grammar; never guesses key digits."""

import re
import time
import uuid

from src.recommendation_models import ActionKind, ProposedAction, SlotRef


class RecommendationParseError(ValueError):
    pass


class RecommendationParser:
    _mulligan = re.compile(r"^替换([1-9]\d*)号位卡牌$")
    _keep_all = "保留全部卡牌"
    _play = re.compile(r"^打出([1-9]\d*)号位(随从|法术|武器|地标|英雄)$")
    _trade = re.compile(r"^(?:交易|锻造|预备)([1-9]\d*)号位卡牌$")
    _destination = re.compile(r"^放置于我方([1-9]\d*)号位$")
    _minion_attack = re.compile(r"^操作([1-9]\d*)号位随从攻击$")
    _hero_attack = re.compile(r"^操作我方英雄攻击$")
    _target = re.compile(
        r"^目标是(?:对方|敌方)([1-9]\d*)号位(?:随从)?$")
    _friendly_hand_target = re.compile(
        r"^目标是(?:己方|我方)([1-9]\d*)号位(?:随从)?$")
    _enemy_hero_targets = {"目标是对方英雄", "目标是敌方英雄"}
    _friendly_hero_targets = {"目标是己方英雄", "目标是我方英雄"}
    _location = re.compile(r"^操作([1-9]\d*)号位地标$")
    _discover = re.compile(
        r"^(?:选择我方([1-4])号位卡牌|选(?:择)?第([1-4])个选项)$")
    # HSAng 时间线提示字：按钮文案可能是纯「回溯/维持」，也可能是带标题的
    # 「回溯时间线/维持时间线」（OCR 也可能把两者读成「回溯」+「时间线」两行，
    # 其中「时间线」非动作行会被过滤，只剩「回溯」）。
    _timeline = re.compile(r"^(回溯|维持)\s*(?:时间线)?$")
    _reference_a_headers = {"打法参考A", "打法参考Ａ"}
    _reference_b_headers = {"打法参考B", "打法参考Ｂ"}

    @classmethod
    def normalize_action_text(cls, text):
        parser = cls()
        lines = parser._reference_a_lines(text or "")
        retained = []
        for index, line in enumerate(lines):
            if (parser._is_action_line(line)
                    or parser._destination.fullmatch(line)
                    or parser._target.fullmatch(line)
                    or parser._friendly_hand_target.fullmatch(line)
                    or line in parser._enemy_hero_targets
                    or "目标" in line
                    or line == "选择卡牌"
                    or (index > 0 and lines[index - 1] == "选择卡牌")):
                retained.append(line)
        return "\n".join(retained)

    def parse(self, ocr, turn_number, log_revision):
        action_text = self.normalize_action_text(ocr.normalized_text)
        lines = self._lines(action_text)
        if not lines:
            raise RecommendationParseError("empty_recommendation")
        action_lines = [line for line in lines if self._is_action_line(line)]
        if self._keep_all in action_lines:
            if len(action_lines) != 1:
                raise RecommendationParseError("ambiguous_actions")
            return self._build(
                ocr, turn_number, log_revision,
                ActionKind.MULLIGAN, mulligan_slots=())
        timeline = [line for line in action_lines
                    if self._timeline_of(line) is not None]
        if timeline:
            # HSAng 时间线提示出现时，面板上其它文字都是上一步已完成动作的残留
            # （盒子先给上一步的推荐、特效完成后弹时间线框、再追加一行时间线字
            # 告诉点哪个按钮）。弹框是模态的——不点掉它什么都做不了，所以共存
            # 内容一律忽略：出现「回溯」就点回溯、出现「维持」就点维持，不再要求
            # 共存的是不是打出/选择。唯一无法判定的是回溯/维持同屏时该点哪个。
            choices = {self._timeline_of(line) for line in timeline}
            if choices == {"undo", "keep"}:
                raise RecommendationParseError("ambiguous_actions")
            action = (ActionKind.TIMELINE_UNDO
                      if "undo" in choices else ActionKind.TIMELINE_KEEP)
            return self._build(ocr, turn_number, log_revision, action)
        mulligans = [self._mulligan.fullmatch(line) for line in action_lines]
        if action_lines and all(match is not None for match in mulligans):
            slots = tuple(sorted({int(match.group(1)) for match in mulligans}))
            return self._build(ocr, turn_number, log_revision,
                               ActionKind.MULLIGAN, mulligan_slots=slots)
        if len(action_lines) != 1:
            raise RecommendationParseError("ambiguous_actions")
        primary = action_lines[0]

        play = self._play.fullmatch(primary)
        if play:
            slot = int(play.group(1))
            card_type = {"随从": "MINION", "法术": "SPELL",
                         "武器": "WEAPON", "地标": "LOCATION",
                         "英雄": "HERO"}[play.group(2)]
            target = None
            if card_type == "SPELL":
                enemy_board_targets = [
                    match for line in lines
                    if (match := self._target.fullmatch(line))
                ]
                friendly_board_targets = [
                    match for line in lines
                    if (match := self._friendly_hand_target.fullmatch(line))
                ]
                enemy_hero_targets = sum(
                    line in self._enemy_hero_targets for line in lines)
                friendly_hero_targets = sum(
                    line in self._friendly_hero_targets for line in lines)
                target_lines = [line for line in lines if "目标" in line]
                if len(target_lines) > 1:
                    raise RecommendationParseError("ambiguous_target")
                target_count = (
                    len(enemy_board_targets) + len(friendly_board_targets)
                    + enemy_hero_targets + friendly_hero_targets)
                if target_lines and target_count != 1:
                    raise RecommendationParseError("unsupported_spell_target")
                if enemy_board_targets:
                    target = SlotRef(
                        "board_slot", "enemy",
                        int(enemy_board_targets[0].group(1)))
                elif friendly_board_targets:
                    target = SlotRef(
                        "board_slot", "friendly",
                        int(friendly_board_targets[0].group(1)))
                elif enemy_hero_targets:
                    target = SlotRef("hero", "enemy")
                elif friendly_hero_targets:
                    target = SlotRef("hero", "friendly")
            elif card_type == "MINION":
                # Card identity in the adapter disambiguates the special
                # battlecries whose friendly slot actually means a hand card.
                target = self._optional_board_or_hero_target(
                    lines, "unsupported_minion_target")
            else:
                self._reject_target_lines(lines)
            destinations = [self._destination.fullmatch(line) for line in lines]
            destinations = [match for match in destinations if match]
            if len(destinations) > 1:
                raise RecommendationParseError("ambiguous_destination")
            destination = (SlotRef("board_slot", "friendly",
                                   int(destinations[0].group(1)))
                           if destinations else None)
            return self._build(
                ocr, turn_number, log_revision, ActionKind.PLAY_CARD,
                source=SlotRef("hand_slot", "friendly", slot),
                destination=destination,
                target=target,
                card_type=card_type)

        trade = self._trade.fullmatch(primary)
        if trade:
            self._reject_target_lines(lines)
            return self._build(
                ocr, turn_number, log_revision, ActionKind.TRADE_CARD,
                source=SlotRef(
                    "hand_slot", "friendly", int(trade.group(1))))

        if primary == "使用英雄技能":
            target = self._optional_board_or_hero_target(
                lines, "unsupported_hero_power_target")
            return self._build(
                ocr, turn_number, log_revision, ActionKind.USE_HERO_POWER,
                source=SlotRef("hero_power", "friendly"), target=target)

        discover = self._discover.fullmatch(primary)
        if discover:
            self._reject_target_lines(lines)
            return self._build(
                ocr, turn_number, log_revision, ActionKind.CHOOSE_DISCOVER,
                source=SlotRef("discover_slot", "friendly",
                               int(discover.group(1) or discover.group(2))))

        attack = self._minion_attack.fullmatch(primary)
        hero_attack = self._hero_attack.fullmatch(primary)
        if attack or hero_attack:
            targets = [self._target.fullmatch(line) for line in lines]
            targets = [match for match in targets if match]
            targets.extend(
                line for line in lines if line in self._enemy_hero_targets)
            if len(targets) > 1 or (not targets and hero_attack):
                raise RecommendationParseError("attack_target_required")
            source = (SlotRef("board_slot", "friendly", int(attack.group(1)))
                      if attack else SlotRef("hero", "friendly"))
            target = None
            if targets:
                target = (SlotRef("hero", "enemy")
                          if targets[0] in self._enemy_hero_targets
                          else SlotRef("board_slot", "enemy",
                                       int(targets[0].group(1))))
            return self._build(
                ocr, turn_number, log_revision, ActionKind.ATTACK,
                source=source, target=target)

        location = self._location.fullmatch(primary)
        if location:
            target = self._optional_board_or_hero_target(
                lines, "unsupported_location_target")
            return self._build(
                ocr, turn_number, log_revision, ActionKind.USE_LOCATION,
                source=SlotRef("board_slot", "friendly",
                               int(location.group(1))),
                target=target)

        if primary == "结束回合":
            self._reject_target_lines(lines)
            return self._build(
                ocr, turn_number, log_revision, ActionKind.END_TURN)
        raise RecommendationParseError("unsupported_recommendation")

    def _lines(self, text):
        translation = str.maketrans("０１２３４５６７８９", "0123456789")
        return [line.strip().translate(translation) for line in text.splitlines()
                if line.strip()]

    def _reference_a_lines(self, text):
        lines = self._lines(text)
        has_reference_a = any(
            line in self._reference_a_headers for line in lines)
        retained = []
        reading_a = not has_reference_a
        for line in lines:
            if line in self._reference_a_headers:
                reading_a = True
                continue
            if line in self._reference_b_headers:
                if reading_a:
                    break
                continue
            if reading_a:
                retained.append(line)
        return retained

    def _is_action_line(self, line):
        if self._timeline_of(line) is not None:
            return True
        return bool(self._mulligan.fullmatch(line) or self._play.fullmatch(line)
                    or self._trade.fullmatch(line)
                    or self._minion_attack.fullmatch(line)
                    or self._hero_attack.fullmatch(line)
                    or self._location.fullmatch(line)
                    or self._discover.fullmatch(line)
                    or line in {self._keep_all, "使用英雄技能", "结束回合"})

    def _timeline_of(self, line):
        match = self._timeline.fullmatch(line)
        if match is None:
            return None
        return "undo" if match.group(1) == "回溯" else "keep"

    def _optional_board_or_hero_target(self, lines, unsupported_code):
        target_lines = [line for line in lines if "目标" in line]
        if len(target_lines) > 1:
            raise RecommendationParseError("ambiguous_target")
        if not target_lines:
            return None
        target_line = target_lines[0]
        enemy_board = self._target.fullmatch(target_line)
        friendly_board = self._friendly_hand_target.fullmatch(target_line)
        if target_line in self._enemy_hero_targets:
            return SlotRef("hero", "enemy")
        if target_line in self._friendly_hero_targets:
            return SlotRef("hero", "friendly")
        if enemy_board:
            return SlotRef(
                "board_slot", "enemy", int(enemy_board.group(1)))
        if friendly_board:
            return SlotRef(
                "board_slot", "friendly", int(friendly_board.group(1)))
        raise RecommendationParseError(unsupported_code)

    @staticmethod
    def _reject_target_lines(lines):
        if any("目标" in line for line in lines):
            raise RecommendationParseError("targeted_action_unsupported")

    @staticmethod
    def _build(ocr, turn_number, log_revision, action, **kwargs):
        action_text = RecommendationParser.normalize_action_text(
            ocr.normalized_text)
        lines = action_text.splitlines()
        markers = [i for i, line in enumerate(lines) if line == "选择卡牌"]
        choice_name = None
        if markers and action in (ActionKind.PLAY_CARD, ActionKind.USE_HERO_POWER):
            if len(markers) != 1 or markers[0] + 1 >= len(lines):
                raise RecommendationParseError("choose_one_name_required")
            choice_name = lines[markers[0] + 1]
            if (RecommendationParser()._is_action_line(choice_name)
                    or "目标" in choice_name or choice_name.startswith("放置于")):
                raise RecommendationParseError("choose_one_name_required")
        return ProposedAction(
            action_id=f"action-{uuid.uuid4()}", frame_id=ocr.frame_id,
            created_at=time.time(), turn_number=turn_number,
            log_revision=log_revision, raw_instruction=action_text,
            normalized_instruction=action_text, action=action,
            ocr_confidence=ocr.confidence, semantic_confidence=1.0,
            choice_name=choice_name,
            **kwargs)
