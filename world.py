import numpy as np
from copy import deepcopy
import traceback
from agents import *
from ui import UIEngine
from time import sleep, time
import click
import logging
from store import AGENT_REGISTRY
from constants import *
import sys

logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)

logger = logging.getLogger(__name__)


class World:
    def __init__(
        self,
        player_1="random_agent",
        player_2="random_agent",
        board_size=None,
        display_ui=False,
        display_delay=2,
        display_save=False,
        display_save_path=None,
        autoplay=False,
    ):
        """
        Initialize the game world

        Parameters
        ----------
        player_1: str
            The registered class of the first player
        player_2: str
            The registered class of the second player
        board_size: int
            The size of the board. If None, board_size = a number between MIN_BOARD_SIZE and MAX_BOARD_SIZE
        display_ui : bool
            Whether to display the game board
        display_delay : float
            Delay between each step
        display_save : bool
            Whether to save an image of the game board
        display_save_path : str
            The path to save the image
        autoplay : bool
            Whether the game is played in autoplay mode
        """
        # Two players
        logger.info("Initialize the game world")
        # Load agents as defined in decorators
        self.player_1_name = player_1
        self.player_2_name = player_2
        if player_1 not in AGENT_REGISTRY:
            raise ValueError(
                f"Agent '{player_1}' is not registered. {AGENT_NOT_FOUND_MSG}"
            )
        if player_2 not in AGENT_REGISTRY:
            raise ValueError(
                f"Agent '{player_2}' is not registered. {AGENT_NOT_FOUND_MSG}"
            )

        p0_agent = AGENT_REGISTRY[player_1]
        p1_agent = AGENT_REGISTRY[player_2]
        logger.info(f"Registering p0 agent : {player_1}")
        self.p0 = p0_agent()
        logger.info(f"Registering p1 agent : {player_2}")
        self.p1 = p1_agent()

        # check autoplay
        if autoplay:
            if not self.p0.autoplay or not self.p1.autoplay:
                raise ValueError(
                    f"Autoplay mode is not supported by one of the agents ({self.p0} -> {self.p0.autoplay}, {self.p1} -> {self.p1.autoplay}). Please set autoplay=True in the agent class."
                )

        self.player_names = {PLAYER_1_ID: PLAYER_1_NAME, PLAYER_2_ID: PLAYER_2_NAME}
        self.dir_names = {
            DIRECTION_UP: DIRECTION_UP_NAME,
            DIRECTION_RIGHT: DIRECTION_RIGHT_NAME,
            DIRECTION_DOWN: DIRECTION_DOWN_NAME,
            DIRECTION_LEFT: DIRECTION_LEFT_NAME,
        }

        # Moves (Up, Right, Down, Left)
        self.moves = ((-1, 0), (0, 1), (1, 0), (0, -1))

        # Opposite Directions
        self.opposites = {0: 2, 1: 3, 2: 0, 3: 1}

        if board_size is None:
            # Random chessboard size
            self.board_size = np.random.randint(MIN_BOARD_SIZE, MAX_BOARD_SIZE)
            logger.info(
                f"No board size specified. Randomly generating size : {self.board_size}x{self.board_size}"
            )
        else:
            self.board_size = board_size
            logger.info(f"Setting board size to {self.board_size}x{self.board_size}")

        # Whose turn to step
        self.turn = 0

        # Time taken by each player
        self.p0_time = []
        self.p1_time = []

        # Cache to store and use the data
        self.results_cache = ()
        # UI Engine
        self.display_ui = display_ui
        self.display_delay = display_delay
        self.display_save = display_save
        self.display_save_path = display_save_path

        # Index in dim2 represents [Up, Right, Down, Left] respectively
        # Record barriers and borders for each block
        self.chess_board = np.zeros((self.board_size, self.board_size, 4), dtype=bool)

        # Set borders
        self.chess_board[0, :, 0] = True
        self.chess_board[:, 0, 3] = True
        self.chess_board[-1, :, 2] = True
        self.chess_board[:, -1, 1] = True

        # Maximum Steps is how far agents will move each turn (at max)
        self.max_step = (self.board_size + 1) // 2
        
        # Fill some random barriers. Few enough to ensure we never start 
        # with a full wall
        num_barriers = self.board_size // 2 - 1

        # Random barriers (symmetric)
        for _ in range(num_barriers):
            pos = np.random.randint(0, self.board_size, size=2)
            r, c = pos
            dir = np.random.randint(0, 4)
            while self.chess_board[r, c, dir]:
                pos = np.random.randint(0, self.board_size, size=2)
                r, c = pos
                dir = np.random.randint(0, 4)
            anti_pos = self.board_size - 1 - pos
            anti_dir = self.opposites[dir]
            anti_r, anti_c = anti_pos
            self.set_barrier(r, c, dir)
            self.set_barrier(anti_r, anti_c, anti_dir)

        # Random start position (symmetric but not overlap)
        self.p0_pos = np.random.randint(0, self.board_size, size=2)
        self.p1_pos = self.board_size - 1 - self.p0_pos

        # Check that initialization does not fall into a weird case, 
        # such as agent stuck in tiny box to begin. Repeat if necessary.
        self.initial_end, _, _ = self.check_endgame()
        while np.array_equal(self.p0_pos, self.p1_pos) or self.initial_end:
            self.p0_pos = np.random.randint(0, self.board_size, size=2)
            self.p1_pos = self.board_size - 1 - self.p0_pos
            self.initial_end, _, _ = self.check_endgame()  
              
        assert(not self.initial_end)

        if display_ui:
            # Initialize UI Engine
            logger.info(
                f"Initializing the UI Engine, with display_delay={display_delay} seconds"
            )
            self.ui_engine = UIEngine(self.board_size, self)
            self.render()

    def get_current_player(self):
        """
        Get the positions of the current player

        Returns
        -------
        tuple of (current_player_obj, current_player_pos, adversary_player_pos)
        """
        if not self.turn:
            return self.p0, self.p0_pos, self.p1_pos
        else:
            return self.p1, self.p1_pos, self.p0_pos

    def update_player_time(self, time_taken):
        """
        Update the time taken by the player

        Parameters
        ----------
        time_taken : float
            Time taken by the player
        """
        if not self.turn:
            self.p0_time.append(time_taken)
        else:
            self.p1_time.append(time_taken)

    def step(self):
        """
        Take a step in the game world.
        Runs the agents' step function and update the game board accordingly.
        If the agents' step function raises an exception, the step will be replaced by a Random Walk.

        Returns
        -------
        results: tuple
            The results of the step containing (is_endgame, player_1_score, player_2_score)
        """
        cur_player, cur_pos, adv_pos = self.get_current_player()

        try:
            # Run the agents step function
            start_time = time()
            next_pos, dir = cur_player.step(
                deepcopy(self.chess_board),
                tuple(cur_pos),
                tuple(adv_pos),
                self.max_step,
            )
            time_taken = time() - start_time
            self.update_player_time(time_taken)

            next_pos = np.asarray(next_pos, dtype=cur_pos.dtype)
            if not self.check_boundary(next_pos):
                raise ValueError("End position {} is out of boundary".format(next_pos))
            if not 0 <= dir <= 3:
                raise ValueError(
                    "Barrier dir should reside in [0, 3], but your dir is {}".format(
                        dir
                    )
                )
            if not self.check_valid_step(cur_pos, next_pos, dir):
                raise ValueError(
                    "Not a valid step from {} to {} and put barrier at {}, with max steps = {}".format(
                        cur_pos, next_pos, dir, self.max_step
                    )
                )
        except BaseException as e:
            ex_type = type(e).__name__
            if (
                "SystemExit" in ex_type and isinstance(cur_player, HumanAgent)
            ) or "KeyboardInterrupt" in ex_type:
                sys.exit(0)
            print(
                "An exception raised. The traceback is as follows:\n{}".format(
                    traceback.format_exc()
                )
            )
            print("Execute Random Walk!")
            next_pos, dir = self.random_walk(tuple(cur_pos), tuple(adv_pos))
            next_pos = np.asarray(next_pos, dtype=cur_pos.dtype)

        # Print out each step
        # print(self.turn, next_pos, dir)
        logger.info(
            f"Player {self.player_names[self.turn]} moves to {next_pos} facing {self.dir_names[dir]}. Time taken this turn (in seconds): {time_taken}"
        )
        if not self.turn:
            self.p0_pos = next_pos
        else:
            self.p1_pos = next_pos
        # Set the barrier to True
        r, c = next_pos
        self.set_barrier(r, c, dir)

        # Change turn
        self.turn = 1 - self.turn

        results = self.check_endgame()
        self.results_cache = results

        # Print out Chessboard for visualization
        if self.display_ui:
            self.render()
            if results[0]:
                # If game ends and displaying the ui, wait for user input
                click.echo("Press a button to exit the game.")
                try:
                    _ = click.getchar()
                except:
                    _ = input()
        return results

    def check_valid_step(self, start_pos, end_pos, barrier_dir):
        """
        Check if the step the agent takes is valid (reachable and within max steps).

        Parameters
        ----------
        start_pos : tuple
            The start position of the agent.
        end_pos : np.ndarray
            The end position of the agent.
        barrier_dir : int
            The direction of the barrier.
        """
        # Endpoint already has barrier or is border
        r, c = end_pos
        if self.chess_board[r, c, barrier_dir]:
            return False
        if np.array_equal(start_pos, end_pos):
            return True

        # Get position of the adversary
        adv_pos = self.p0_pos if self.turn else self.p1_pos

        # BFS
        state_queue = [(start_pos, 0)]
        visited = {tuple(start_pos)}
        is_reached = False
        # BFS to check if the end_pos is reachable
        while state_queue and not is_reached:
            # Get the current position and step
            cur_pos, cur_step = state_queue.pop(0)
            r, c = cur_pos
            # If the current step is equal than max_step, break
            if cur_step == self.max_step:
                break   
            # Check all possible moves
            for dir, move in enumerate(self.moves):
                # If there is a barrier, skip
                if self.chess_board[r, c, dir]:
                    continue
                # If the next position is the adversary, skip
                next_pos = cur_pos + move
                # Check if the next position is valid
                if np.array_equal(next_pos, adv_pos) or tuple(next_pos) in visited:
                    continue
                # Check if the next position is the end position
                if np.array_equal(next_pos, end_pos):
                    is_reached = True
                    break
                # Add the next position to the queue
                visited.add(tuple(next_pos))
                # Add the next state to the queue
                state_queue.append((next_pos, cur_step + 1))

        return is_reached

    def check_endgame(self):
        """
        Check if the game ends and compute the current score of the agents.

        Returns
        -------
        is_endgame : bool
            Whether the game ends.
        player_1_score : int
            The score of player 1.
        player_2_score : int
            The score of player 2.
        """
        # Union-Find
        father = dict()
        for r in range(self.board_size):
            for c in range(self.board_size):
                father[(r, c)] = (r, c)

        def find(pos):
            # Path compression
            if father[pos] != pos:
                # If not root, recursively find the root
                father[pos] = find(father[pos])
            return father[pos]

        def union(pos1, pos2):
            father[pos1] = pos2

        for r in range(self.board_size):
            for c in range(self.board_size):
                # Check down and right
                for dir, move in enumerate(
                    # Only check down and right
                    self.moves[1:3]
                ):  # Only check down and right
                    # If there is a barrier, skip
                    if self.chess_board[r, c, dir + 1]:
                        continue
                    # If the next position is the adversary, skip
                    pos_a = find((r, c))
                    # Check if the next position is valid
                    pos_b = find((r + move[0], c + move[1]))
                    # Check if the next position is the end position
                    if pos_a != pos_b:
                        union(pos_a, pos_b)

        for r in range(self.board_size):
            for c in range(self.board_size):
                find((r, c))
        p0_r = find(tuple(self.p0_pos))
        p1_r = find(tuple(self.p1_pos))
        p0_score = list(father.values()).count(p0_r)
        p1_score = list(father.values()).count(p1_r)
        if p0_r == p1_r:
            return False, p0_score, p1_score
        player_win = None
        win_blocks = -1
        if p0_score > p1_score:
            player_win = 0
            win_blocks = p0_score
        elif p0_score < p1_score:
            player_win = 1
            win_blocks = p1_score
        else:
            player_win = -1  # Tie
        if player_win >= 0:
            logging.info(
                f"Game ends! Player {self.player_names[player_win]} wins having control over {win_blocks} blocks!"
            )
        else:
            logging.info("Game ends! It is a Tie!")
        return True, p0_score, p1_score

    def check_boundary(self, pos):
        r, c = pos
        return 0 <= r < self.board_size and 0 <= c < self.board_size

    def set_barrier(self, r, c, dir):
        # Set the barrier to True
        self.chess_board[r, c, dir] = True
        # Set the opposite barrier to True
        move = self.moves[dir]
        self.chess_board[r + move[0], c + move[1], self.opposites[dir]] = True

    def random_walk(self, my_pos, adv_pos):
        """
        Randomly walk to the next position in the board.

        Parameters
        ----------
        my_pos : tuple
            The position of the agent.
        adv_pos : tuple
            The position of the adversary.
        """
        steps = np.random.randint(0, self.max_step + 1)

        # Pick steps random but allowable moves
        for _ in range(steps):
            r, c = my_pos

            # Build a list of the moves we can make
            allowed_dirs = [ d                                
                for d in range(0,4)                                      # 4 moves possible
                if not self.chess_board[r,c,d] and                       # chess_board True means wall
                not adv_pos == (r+self.moves[d][0],c+self.moves[d][1])]  # cannot move through Adversary

            if len(allowed_dirs)==0:
                # If no possible move, we must be enclosed by our Adversary
                break

            random_dir = allowed_dirs[np.random.randint(0, len(allowed_dirs))]

            # This is how to update a row,col by the entries in moves 
            # to be consistent with game logic
            m_r, m_c = self.moves[random_dir]
            my_pos = (r + m_r, c + m_c)

        # Final portion, pick where to put our new barrier, at random
        r, c = my_pos
        # Possibilities, any direction such that chess_board is False
        allowed_barriers=[i for i in range(0,4) if not self.chess_board[r,c,i]]
        # Sanity check, no way to be fully enclosed in a square, else game already ended
        assert len(allowed_barriers)>=1 
        dir = allowed_barriers[np.random.randint(0, len(allowed_barriers))]

        return my_pos, dir

    def render(self, debug=False):
        """
        Render the game board using the UI Engine
        """
        self.ui_engine.render(self.chess_board, self.p0_pos, self.p1_pos, debug=debug)
        sleep(self.display_delay)


if __name__ == "__main__":
    world = World()
    is_end, p0_score, p1_score = world.step()
    while not is_end:
        is_end, p0_score, p1_score = world.step()
    print(p0_score, p1_score)
