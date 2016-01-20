import numpy as np
from transition_bank import TransitionBank
import random
import cPickle as pickle
from lasagne.layers import get_all_param_values
from lasagne.layers import set_all_param_values
from vizia import  *
import itertools as it
from random import choice

class IdentityImageConverter:
    def __init__(self, source):
        self._source = source

    def convert(self, img):
        return  img

    def get_screen_width(self):
        return self._source.get_screen_width()

    def get_screen_height(self):
        return self._source.get_screen_height()

    def get_screen_channels(self):
        return self._source.get_screen_channels()


class Float32ImageConverter(IdentityImageConverter):
    def __init__(self, source):
        self._source = source

    def convert(self, img):
        return  np.float32(img)/255.0

def default_actions_generator(the_game):
    n = the_game.get_available_buttons_size()
    actions = []
    for perm in it.product([0, 1], repeat=n):
        actions.append(list(perm))
    return actions

class QEngine:
    def __init__(self, **kwargs):
        self._qengine_args = kwargs
        self._initialize(**kwargs)
        kwargs["game"] = None

    def _initialize(self, game, evaluator, actions_generator=None, gamma=0.7, batch_size=500, update_frequency=500,
                 history_length=1, bank = None, bank_capacity=10000, start_epsilon=1.0, end_epsilon=0.0,
                 epsilon_decay_start_step=100000, epsilon_decay_steps=100000, reward_scale = 1.0, max_reward = 1.0, 
                 image_converter=None, skiprate = 1, shaping_on = False):
    # Line that makes sublime collapse code correctly

        if image_converter:
            self._image_converter = image_converter(game)
        else:
            self._image_converter = Float32ImageConverter(game)
        print max_reward
        self._max_reward = np.float32(max_reward) 
        self._reward_scale = reward_scale
        self._game = game
        self._gamma = gamma
        self._batch_size = batch_size
        self._history_length = max(history_length, 1)
        self._update_frequency = update_frequency
        self._epsilon = max(min(start_epsilon, 1.0), 0.0)
        self._end_epsilon = min(max(end_epsilon, 0.0), self._epsilon)
        self._epsilon_decay_stride = (self._epsilon - end_epsilon) / epsilon_decay_steps
        self._epsilon_decay_start = epsilon_decay_start_step
        self._skiprate = max(skiprate, 1)
        self._shaping_on = shaping_on

        if self._shaping_on:
            self._last_shaping_reward = 0

        self.learning_mode = True
        if bank and type(bank) == type(TransitionBank):
            self._transitions = bank
        else:
            self._transitions = TransitionBank(bank_capacity)
        self._steps = 0
        if actions_generator == None:
            self._actions = default_actions_generator(game)
        else:
            self._actions = actions_generator(game)
        self._actions_num = len(self._actions)
        self._actions_stats = np.zeros([self._actions_num], np.int)

        
        # change img_shape according to the history size
        self._channels = self._image_converter.get_screen_channels()
        img_shape = [self._channels, self._image_converter.get_screen_height(), self._image_converter.get_screen_width()]
        
        if history_length > 1:
            img_shape[0] *= history_length
        
        state_format = [img_shape, game.get_available_game_variables_size()]
        self._evaluator = evaluator(state_format, len(self._actions), batch_size, self._gamma)
        self._current_image_state = np.zeros(img_shape, dtype=np.float32)

        if game.get_available_game_variables_size() > 0:
            self._misc_state_included = True
            self._current_misc_state = np.zeros(game.get_available_game_variables_size(), dtype=np.float32)
        else:
            self._misc_state_included = False

   
    def _update_state(self, raw_state):
        img = self._image_converter.convert(raw_state.image_buffer)
        if self._misc_state_included:
            misc = np.float32(raw_state.game_variables)
        if self._history_length > 1:
            self._current_image_state[0:-self._channels] = self._current_image_state[self._channels:]
            self._current_image_state[-self._channels:] = img
            if self._misc_state_included:
                self._current_misc_state[0:-1] = self._current_misc_state[1:]
                self._current_misc_state[-1] = misc

        else:
            self._current_image_state[:] = img
            if self._misc_state_included:
                self._current_misc_state = misc
        
    def new_episode(self):
        self._game.new_episode()
        self.reset_state()
        #raw_state = self._game.get_state()
        #self._update_state(raw_state)        

    # Return current state including history
    def _current_state(self):
    	if self._misc_state_included:
            s = [self._current_image_state, self._current_misc_state]
        else:
            s = [self._current_image_state]
        return s

    # Return current state's COPY including history.
    def _current_state_copy(self):
        if self._misc_state_included:
            s = [self._current_image_state.copy(), self._current_misc_state.copy()]
        else:
            s = [self._current_image_state.copy()]
        return s

    # Returns index of the best action. State should include history.
    def _choose_action_index(self, state):
    	return self._evaluator.best_action(state)

    # Sets the whole state to zeros. 
    def reset_state(self):
        self._current_image_state.fill(0.0)
        if self._misc_state_included:
            self._current_misc_state.fill(0.0)

    def make_step(self):
    	raw_state = self._game.get_state()
        self._update_state(raw_state)
        a = self._choose_action_index(self._current_state())
    	self._actions_stats[a] += 1
    	self._game.make_action(self._actions[a], self._skiprate)

    # UPDATES state (hisotry). Returns the best action. State should not include history
    def best_action(self, state):
        raw_state = self._game.get_state()
        self._update_state(raw_state)
        return self._actions[self._choose_action_index(self._current_state())]

    def make_random_step(self):
        self._game.make_action(choice(self._actions), self._skiprate)

    def make_learning_step(self):
        self._steps += 1
       	# epsilon decay:
        if self._steps > self._epsilon_decay_start and self._epsilon > 0:
	        self._epsilon -= self._epsilon_decay_stride
	        self._epsilon = max(self._epsilon, 0)

	    # Copy is made because it will be stored in transitions
        s = self._current_state_copy();

        # With probability epsilon choose a random action:
        if self._epsilon >= random.random():
            a = random.randint(0, len(self._actions) - 1)
        else:
            a = self._evaluator.best_action(s)
        self._actions_stats[a] += 1

        # make action and get the reward
        r = self._game.make_action(self._actions[a], self._skiprate)
        r = np.float32(r)
        if self._shaping_on:
            sr = np.float32(doom_fixed_to_double(self._game.get_game_variable(GameVariable.USER1)))
            r += sr - self._last_shaping_reward
            self._last_shaping_reward = sr
        r = r*self._reward_scale

        if self._max_reward:
            r = min(r, self._max_reward)
        #update state s2 accordingly
        if self._game.is_episode_finished():
            # terminal state
            s2 = None
        else:
            raw_state = self._game.get_state()
            self._update_state( raw_state )
            s2 = self._current_state_copy()

        self._transitions.add_transition(s, a, s2, r)
    
        # Perform q-learning once for a while
        if self._steps % self._update_frequency[0] == 0 and self._steps > self._batch_size:
            for i in range(self._update_frequency[1]):
                self._evaluator.learn(self._transitions.get_sample(self._batch_size))
      
    # Runs a single episode in current mode. It ignores the mode if learn==true/false
    def run_episode(self, learn = None):
        self.new_episode()
       	if self.learning_mode and learn != False:
            raw_state = self._game.get_state()
            self._update_state(raw_state)
            while not self._game.is_episode_finished():
                self.make_learning_step()
       	else:
	        while not self._game.is_episode_finished():
	            self.make_step()

        return np.float32(self._game.get_summary_reward())

#################################### UTIL STUFF ####################################
    def get_actions_stats(self, clear=False, norm=True):
        stats = self._actions_stats.copy()
        if norm:
            stats = stats / np.float32(self._actions_stats.sum())
            stats[stats == 0.0] = -1
            stats = np.around(stats, 3)

        if clear:
            self._actions_stats.fill(0)
        return stats

    def get_steps(self):
        return self._steps

    def get_epsilon(self):
        return self._epsilon

    def get_network(self):
        return self._evaluator.get_network()

    def set_epsilon(self, eps):
        self._epsilon = eps
    # Saves network weights to a file
    def save_params(self, filename):
        print "Saving network weights to " + filename +"..."
        params = get_all_param_values( self._evaluator.get_network() )
        pickle.dump( params, open( filename, "wb" ) )
        print "Saving finished."
    
    # Loads network weights from the file
    def load_params(self, filename):
        print "Loading network weights from " + filename + "..."
        params = pickle.load( open( filename, "rb" ) )
        set_all_param_values( self._evaluator.get_network(), params )
        print "Loading finished."

     # Loads the whole engine with params from file
    @staticmethod
    def load( game, filename):
        print "Loading qengine from " + filename + "..."
       
        params = pickle.load( open( filename, "rb" ) )
        
        qengine_args = params[0]
        network_params = params[1]

        qengine_args["game"] = game
        qengine = QEngine(**qengine_args)
        set_all_param_values( qengine._evaluator.get_network(), network_params )
        
        print "Loading finished."
        return qengine

    # Saves the whole engine with params to a file
    def save(self, filename):
        print "Saving qengine to " + filename +"..."

        network_params = get_all_param_values( self._evaluator.get_network() )
        params = [self._qengine_args, network_params]
        pickle.dump( params, open( filename, "wb" ) )
            
        print "Saving finished."
