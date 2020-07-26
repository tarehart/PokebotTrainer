from dataclasses import dataclass
from random import randint
from threading import Thread
from time import sleep
from typing import List, Dict
from pathlib import Path

from rlbot.agents.base_script import BaseScript
from rlbot.matchconfig.match_config import MatchConfig, PlayerConfig, MutatorConfig, EmptyPlayerSlot
from rlbot.parsing.bot_config_bundle import BotConfigBundle
from rlbot.parsing.directory_scanner import scan_directory_for_bot_configs
from rlbot.setup_manager import SetupManager
from rlbot.utils.structures.game_data_struct import PlayerInfo
from rlbot_action_server.bot_action_broker import BotActionBroker, run_action_server, find_usable_port
from rlbot_action_server.bot_holder import set_bot_action_broker
from rlbot_action_server.models import BotAction, AvailableActions, ActionChoice, ApiResponse
from rlbot_twitch_broker_client import Configuration, RegisterApi, ApiClient, ActionServerRegistration
from rlbot_twitch_broker_client.defaults import STANDARD_TWITCH_BROKER_PORT
from urllib3.exceptions import MaxRetryError

SPAWN = 'spawn'
BOT_DIRECTORY = Path(__file__).parent / 'bots'
LIFESPAN = 20

class MyActionBroker(BotActionBroker):
    def __init__(self, script):
        self.script = script

    def get_actions_currently_available(self) -> List[AvailableActions]:
        return self.script.get_actions_currently_available()

    def set_action(self, choice: ActionChoice):
        self.script.process_choice(choice.action)
        return ApiResponse(200, f"I choose {choice.action.description}")


@dataclass
class ActiveBot:
    name: str
    team: int
    spawn_id: int
    join_time: float
    bundle:  BotConfigBundle


def highlight_team_color(text: str, team: int) -> str:
    team_class = 'blue' if team == 0 else 'orange'
    return f'<span class="player-name {team_class}">{text}</span>'


def player_config_from_active_bot(active_bot: ActiveBot):
    if active_bot is None:
        return EmptyPlayerSlot()
    return create_player_config(active_bot.name, active_bot.team, active_bot.spawn_id, active_bot.bundle.config_path)


def create_player_config(name: str, team: int, spawn_id: int, config_path: str):
    player_config = PlayerConfig()
    player_config.bot = True
    player_config.rlbot_controlled = True
    player_config.bot_skill = 1
    player_config.human_index = 0
    player_config.name = name
    player_config.team = team
    player_config.spawn_id = spawn_id
    player_config.config_path = config_path
    return player_config


class PokebotTrainer(BaseScript):

    def __init__(self):
        super().__init__("Pokebot Trainer")
        self.action_broker = MyActionBroker(self)
        self.known_players: List[PlayerInfo] = []
        self.active_bots: List[ActiveBot] = []
        self.available_bots: Dict[str, BotConfigBundle] = {b.name: b for b in self.get_bots()}
        self.available_bot_names: List[str] = list(self.available_bots.keys())
        self.available_bot_names.sort()
        self.logger.info(f"Pokebot trainer has: {self.available_bots.keys()}")
        self.setup_manager = SetupManager()
        self.ready = False

    def heartbeat_connection_attempts_to_twitch_broker(self, port):
        register_api_config = Configuration()
        register_api_config.host = f"http://127.0.0.1:{STANDARD_TWITCH_BROKER_PORT}"
        twitch_broker_register = RegisterApi(ApiClient(configuration=register_api_config))
        while True:
            try:
                twitch_broker_register.register_action_server(
                    ActionServerRegistration(base_url=f"http://127.0.0.1:{port}"))
            except MaxRetryError:
                self.logger.warning('Failed to register with twitch broker, will try again...')
            sleep(10)

    def get_bots(self):
        return scan_directory_for_bot_configs(BOT_DIRECTORY)

    def get_actions_currently_available(self) -> List[AvailableActions]:
        actions = []
        for name in self.available_bot_names:
            blue_text = highlight_team_color(f"blue {name}", 0)
            actions.append(BotAction(description=f"Spawn {blue_text}", action_type=SPAWN, data={'name': name, 'team': 0}))
        for name in self.available_bot_names:
            orange_text = highlight_team_color(f"orange {name}", 1)
            actions.append(BotAction(description=f"Spawn {orange_text}", action_type=SPAWN, data={'name': name, 'team': 1}))
        return [AvailableActions(self.name, None, actions)]

    def process_choice(self, choice: BotAction):

        if not self.setup_manager.has_started:
            self.logger.error(f"Tried to {choice.description} before the setup manager was fully started!")
            return

        if choice.action_type == SPAWN:
            name = choice.data['name']
            team = choice.data['team']
            bundle = self.available_bots[name]

            names = set([ab.name for ab in self.active_bots if ab is not None])
            unique_name = name[:31]
            count = 2
            while unique_name in names:
                unique_name = f'{name[:27]} ({count})'  # Truncate at 27 because we can have up to '(10)' appended
                count += 1

            new_bot = ActiveBot(unique_name, team, randint(1, 2**31 - 1), self.game_tick_packet.game_info.seconds_elapsed, bundle)
            try:
                none_index = self.active_bots.index(None)
                self.active_bots[none_index] = new_bot
            except ValueError:
                self.active_bots.append(new_bot)
            self.relaunch_bots()

    def relaunch_bots(self):
        match_config = MatchConfig()
        match_config.player_configs = [player_config_from_active_bot(ab) for ab in self.active_bots]
        match_config.game_mode = 'Soccer'
        match_config.game_map = 'DFHStadium'
        match_config.existing_match_behavior = 'Continue And Spawn'
        match_config.mutators = MutatorConfig()

        self.setup_manager.load_match_config(match_config)
        self.setup_manager.start_match()
        self.setup_manager.launch_bot_processes()
        self.setup_manager.try_recieve_agent_metadata()

    def start(self):
        port = find_usable_port(9886)
        Thread(target=run_action_server, args=(port,), daemon=True).start()
        set_bot_action_broker(self.action_broker)

        Thread(target=self.heartbeat_connection_attempts_to_twitch_broker, args=(port,), daemon=True).start()

        self.setup_manager.connect_to_game()
        self.ready = True

        while True:
            self.get_game_tick_packet()
            self.setup_manager.try_recieve_agent_metadata()
            game_seconds = self.game_tick_packet.game_info.seconds_elapsed

            needs_relaunch = False
            next_bots = []
            for b in self.active_bots:
                if b is not None and game_seconds > b.join_time + LIFESPAN:
                    next_bots.append(None)
                    needs_relaunch = True
                else:
                    next_bots.append(b)

            if needs_relaunch:
                while len(next_bots) > 0 and next_bots[-1] is None:
                    next_bots.pop()  # Get rid of any trailing None values.
                self.active_bots = next_bots
                self.relaunch_bots()

            sleep(0.1)



if __name__ == '__main__':
    trainer = PokebotTrainer()
    trainer.start()
