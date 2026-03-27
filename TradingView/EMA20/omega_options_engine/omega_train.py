import logging
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
from omega_env import OmegaOptionsEnv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def evaluate_brain(model, env, num_episodes=5):
    """Run a few deterministic test episodes to see how the trained AI behaves."""
    logger.info("--- Evaluating Trained Omega Brain ---")
    
    for episode in range(num_episodes):
        obs = env.reset()
        done = False
        total_pnl = 0
        trades_taken = 0
        
        # SB3 vectorized environments return lists/arrays
        while not done:
            action, _states = model.predict(obs, deterministic=True)
            obs, rewards, dones, infos = env.step(action)
            total_pnl += rewards[0]
            if action[0] != 0:
                trades_taken += 1
            done = dones[0]
            
        logger.info(f"Test Pass {episode+1} | Total Options Traded: {trades_taken} | Cumulated RL Reward: {total_pnl:.2f}")

def main():
    logger.info("Initializing the RL Training Sequence...")
    
    # 1. Instantiate the vectorized Environment
    # We use a single vectorized environment for continuous state space stepping
    vec_env = make_vec_env(OmegaOptionsEnv, n_envs=1, env_kwargs={'db_path': 'omega_telemetry.db'})
    
    # 2. Compile the Neural Network (Proximal Policy Optimization)
    # MLP Policy = Standard Feedforward Neural Network
    logger.info("Compiling PPO Neural Network Matrix...")
    model = PPO("MlpPolicy", vec_env, verbose=1, learning_rate=0.0003)
    
    # 3. Train the Brain
    # 100,000 timesteps is a solid baseline for behavioral cloning essentially
    training_steps = 100000
    logger.info(f"Beginning Deep Reinforcement Learning for {training_steps} timesteps...")
    
    model.learn(total_timesteps=training_steps)
    
    # 4. Serialize and Save the Weights
    save_path = "omega_brain"
    model.save(save_path)
    logger.info(f"Training Complete. Neural net weights serialized to {save_path}.zip")
    
    # 5. Evaluate
    evaluate_brain(model, vec_env, num_episodes=3)

if __name__ == "__main__":
    main()
