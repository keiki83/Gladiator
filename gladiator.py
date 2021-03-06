import MalmoPython
import os
import sys
import time
import random
import json
import math
import cPickle as pickle
from sarsa import perform_trial
from collections import namedtuple


State = namedtuple('State', 'distance, hit, mob_hit, health, air')
EntityInfo = namedtuple('EntityInfo', 'x, y, z, yaw, pitch, name, colour, variation, quantity, life')
EntityInfo.__new__.__defaults__ = (0, 0, 0, 0, 0, "", "", "", 1, 0)


# trial parameters
DEFAULT_NUM_TRIALS = 1000
EPSILON = 0.5
EPSILON_DECAY = 0.99
ALPHA = 0.2
GAMMA = 0.9
AGENT = "Gladiator"
DEFAULT_MISSION = "arena2.xml"
SIDE_ATTACK = True
ENABLE_ENEMY_DISTANCE_SATURATION = True
ENEMY_DISTANCE_SATURATION_LEVEL = 10
STATISTICS_OUTPUT_FILE = "_statistics.txt"
STATISTICS = {"reward":0, "kill":0, "action":0}
QTABLE_FILENAME = "q_table.p"
MOB_TYPE = "Zombie"
MOB_START_LOCATION = [(8,64,8),(-8,64,8),(8,64,-8),(-8,64,-8)]
MOB_SPAWN_DISTANCE_LIMIT = 4
MOVESPEED = 0.6
ACTION_DELAY = 0.1
SPAWN_DELAY = 0.5
TURN_RATE_SCALE = 90.
HEALTH_THRESHOLDS = ((20., 0), (15., 1), (10., 2), (5., 3), (0., 4))
actions = ["move " + str(MOVESPEED) , "move " + str(-1*MOVESPEED), "strafe " + str(MOVESPEED), "strafe " + str(-1*MOVESPEED), "attack 1"]
#translate actions to front=1/back=2/left=4/right=8 to correspond w/air
air_actions = {actions[0]:1, actions[3]:2, actions[2]:4, actions[1]:8}
air_indices = { "north":{1:1, 3:4, 5:8, 7:2},
                "south":{1:2, 3:8, 5:4, 7:1},
                "west":{1:8, 3:1, 5:2, 7:4},
                "east":{1:4, 3:2, 5:1, 7:8} }

# rewards
DEATH_REWARD = -0.
PROXIMITY_REWARD = 10
ENEMY_HIT_REWARD = 30
DAMAGE_REWARD = -ENEMY_HIT_REWARD / HEALTH_THRESHOLDS[-1][1] #scale penalty based on relative health
WALL_REWARD = -10
TICK_REWARD = 5
#SATURATION_REWARD = -MAX_ENEMY_PROXIMITY_REWARD



# helper functions
def checkMobHit(lifePrime):
	try:
		hit = (lifePrime < checkMobHit.life)
		checkMobHit.life = lifePrime
	except AttributeError:
		checkMobHit.life = lifePrime
		hit = False
	return hit

def checkHit(lifePrime):
	try:
		hit = (lifePrime < checkHit.life)
		checkHit.life = lifePrime
		if lifePrime <= 0:
			agent_host.sendCommand("chat I'm dead!")
	except AttributeError:
		checkHit.life = lifePrime
		hit = False
	return hit

def countMobs(entities):
	mobCount = 0
	for ent in entities:
		if ent.name == MOB_TYPE:
			mobCount += 1
	return mobCount

def findUs(entities):
	for ent in entities:
		if ent.name == MOB_TYPE:
			continue
		else:
			return ent

def normalize_yaw(yaw):
    original_yaw = yaw
    if yaw > 180.:
        factor = math.floor((yaw + 180.) / 360.)
    elif yaw < -180:
        factor = math.ceil((yaw-180.) / 360.)
    else:
        return yaw
    yaw -= 360. * factor
    return yaw

def yaw_to_dir(yaw):
    if yaw >= -45. and yaw < 45:
        return "south"
    elif yaw >= 45. and yaw < 135.:
        return "west"
    elif yaw >= -135. and yaw < 45:
        return "east"
    else:
        return "north"

def lookAtNearestEntity(entities):
    us = findUs(entities)
    current_yaw = us.yaw
    closestEntity = 0
    entityDistance = sys.float_info.max
    for i,ent in enumerate(entities):
        if ent.name == MOB_TYPE:
            #check distance from us and get the lowest
            dist = math.sqrt((ent.x - us.x)*(ent.x - us.x) + (ent.z - us.z)*(ent.z - us.z))
            if(dist < entityDistance):
                closestEntity = i
                entityDistance = dist
    if closestEntity == 0:
        return 0
    best_yaw = math.degrees(math.atan2(entities[closestEntity].z - us.z, entities[closestEntity].x - us.x)) - 90
    difference = normalize_yaw(best_yaw - current_yaw);
    #while difference < -180:
#        difference += 360;
#    while difference > 180:
#        difference -= 360;
    difference /= TURN_RATE_SCALE;
    threshhold = 0.0
    if difference < threshhold and difference > 0:
    	difference = threshhold
    elif difference > -1*threshhold and difference < 0:
    	difference = -1*threshhold

    agent_host.sendCommand("turn " + str(difference))

def extractAirState(grid, yaw):
    s = 0
    for k,v in air_indices[yaw_to_dir(normalize_yaw(yaw))].items():
        if grid[k] == u'air':
            s = s + v
    return s

#assumes there is a single mob near the agent. gets it x and z coordinates and returns the x and z coordinates relative to the agent
def extractMobState(entities):
    #res = 4
    life = 0
    for entity in entities:
        if entity.name == AGENT:
            m_x = a_x = entity.x
            m_z = a_z = entity.z
        elif entity.name == MOB_TYPE:
            m_x = entity.x
            m_z = entity.z
            life = entity.life
    distance = round(math.sqrt((m_x - a_x)**2 + (m_z - a_z)**2))
    if ENABLE_ENEMY_DISTANCE_SATURATION:
        if abs(distance) > ENEMY_DISTANCE_SATURATION_LEVEL:
            distance = math.copysign(ENEMY_DISTANCE_SATURATION_LEVEL, distance)
        #if abs(z) > ENEMY_DISTANCE_SATURATION_LEVEL:
        #    z = math.copysign(ENEMY_DISTANCE_SATURATION_LEVEL, z)
    return life,distance

def extractHealthState(health):
    for threshold,state in HEALTH_THRESHOLDS:
        if health >= threshold: return state

# this is where the rewards are counted and the state is determined
def getState():
    world_state = agent_host.getWorldState()
    while world_state.is_mission_running and len(world_state.observations) == 0:
        time.sleep(0.1)
        world_state = agent_host.getWorldState()
    if world_state.is_mission_running:
        msg = world_state.observations[-1].text
        ob = json.loads(msg)
        entities = [EntityInfo(**k) for k in ob[u'entities']]
       	#check for hit here
        health = ob[u'Life']
       	hit = checkHit(health)
        mob_life, mob_distance = extractMobState(entities)
        mob_hit = checkMobHit(mob_life)
        air_state = extractAirState(ob[u'floorAll'], findUs(entities).yaw);
        s = State(distance=mob_distance, hit=hit, mob_hit=mob_hit, health=extractHealthState(health), air=air_state)
    else:
        s = None
        ob = None
    return s, ob

def isTerminal(state):
    return (state is None) or (not world_state.is_mission_running) or (checkHit.life <= 0.)  #or (state.x == 0. and state.z == 0.)

def calculate_reward(state, s_prime, action):
    reward = 0
    if state.hit:
    	reward += DAMAGE_REWARD * state.health
    if state.mob_hit:
    	reward += ENEMY_HIT_REWARD
    #health related rewards
    if checkHit.life <= 0.:
        reward += DEATH_REWARD

    #distance related rewards
    reward += PROXIMITY_REWARD * (s_prime.distance - state.distance)
    for k,v in air_actions.items():
        if k == action and not v & state.air:
            reward += WALL_REWARD
#    if not (state.x == 0 and state.z == 0):
#        if abs(state.x) >= ENEMY_DISTANCE_SATURATION_LEVEL or abs(state.z) >= ENEMY_DISTANCE_SATURATION_LEVEL:
#            reward += SATURATION_REWARD
#        else:
#            reward += (MAX_ENEMY_PROXIMITY_REWARD / math.sqrt(state.x**2 + state.z**2))
#    else:
#        reward += MAX_ENEMY_PROXIMITY_REWARD

    #movement related penalties
    reward += TICK_REWARD
    #if action == "move " + str(MOVESPEED) and not hit:
    #	reward += 1
    #if action != "move " + str(MOVESPEED) and math.sqrt(state.x**2 + state.z**2) > ENEMY_DISTANCE_SATURATION_LEVEL:
    #	reward += -1

    global STATISTICS
    STATISTICS["reward"] += reward
    #cumulative_reward = cumulative_reward + reward
    return reward

def spawn_mob(agent):
    min_index = 0
    min_distance = float("inf")
    valid_corners = MOB_START_LOCATION[:]
    for x,y,z in valid_corners:
        if abs(agent.x - x) <= MOB_SPAWN_DISTANCE_LIMIT and abs(agent.z - z) <= MOB_SPAWN_DISTANCE_LIMIT:
            valid_corners.remove((x,y,z))
    location = random.choice(valid_corners)
    agent_host.sendCommand("chat /summon {0} {1} {2} {3} {4}".format(MOB_TYPE, location[0], location[1], location[2], "{IsBaby:0}")) #summon mob
    time.sleep(SPAWN_DELAY)

def do_action(state, action):
    global STATISTICS
    STATISTICS["action"] += 1
    #ticksElapsed = ticksElapsed + 1

    #if "strafe" in action and SIDE_ATTACK:
    #    agent_host.sendCommand("attack 1")

    s_prime, ob = getState()
    if not state is None:

        # turn towards the nearest zombie
        entities = [EntityInfo(**k) for k in ob[u'entities']]
        lookAtNearestEntity(entities)

        # take action
        agent_host.sendCommand("move 0")
        agent_host.sendCommand("strafe 0")
        agent_host.sendCommand(action)

        reward = calculate_reward(state, s_prime, action)

        #automatically spawn a new mob if there is none
        if(countMobs(entities) == 0): #summon mobs if their are no more on the field
            us = findUs(entities)
            spawn_mob(us)
            #a = ( int(random.random() * 4) % 4)
            #agent_host.sendCommand("chat /summon {0} {1} {2} {3} {4}".format(MOB_TYPE, MOB_START_LOCATION[a][0], MOB_START_LOCATION[a][1], MOB_START_LOCATION[a][2], "{IsBaby:0}")) #summon mob
            #global killCount
            STATISTICS["kill"] += 1
            #killCount = killCount + 1



        #taunt enemy
        x = random.random()
        if x < 0.01:
        	if x < 0.0025:
        		agent_host.sendCommand("chat Are you not entertained?!")
        	elif x < 0.005:
        		agent_host.sendCommand("chat You are weak!")
        	else:
        	    agent_host.sendCommand("chat Come at me!!!")

    else:
        reward = 0
    time.sleep(ACTION_DELAY)
    return reward, s_prime, actions

def load_mission(fileName):
    # load mission from file
    my_mission = None
    my_mission_record = None
    with open(fileName, 'r') as f:
        print "Loading mission from %s" % fileName
        mission_xml = f.read()
        my_mission = MalmoPython.MissionSpec(mission_xml, True)
    #my_mission.forceWorldReset()    # force reset fixes back to back testing
        my_mission_record = MalmoPython.MissionRecordSpec()
    return my_mission, my_mission_record

def start_mission(agent_host, mission, mission_record):
# Attempt to start a mission:
    max_retries = 3
    for retry in range(max_retries):
    	try:
    		agent_host.startMission( mission, mission_record )
    		break
    	except RuntimeError as e:
    		if retry == max_retries - 1:
    			print "Error starting mission:",e
    			exit(1)
    		else:
    			time.sleep(2)
    # Loop until mission starts:
    print "\nWaiting for the mission to start ",
    world_state = agent_host.getWorldState()
    while not world_state.has_mission_begun:
    	sys.stdout.write(".")
    	time.sleep(0.1)
    	world_state = agent_host.getWorldState()
    	for error in world_state.errors:
    		print "Error:",error.text
    print "\nMission running\n"



if __name__ == "__main__":
    # start of execution
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)  # flush print output immediately
    # Create default Malmo objects:
    agent_host = MalmoPython.AgentHost()
    try:
    	agent_host.parse( sys.argv )
    except RuntimeError as e:
    	print 'ERROR:',e
    	print agent_host.getUsage()
    	exit(1)
    if agent_host.receivedArgument("help"):
    	print agent_host.getUsage()
    	exit(0)
    my_mission, my_mission_record = load_mission(DEFAULT_MISSION)

    # Main loop:
    # Loop until mission ends:
    try :
        q_table = pickle.load(open(QTABLE_FILENAME, "rb"))
    except IOError as e:
        q_table = {}
        print 'ERROR:', e
    #rewards = []
    #cumulative_reward = 0.
    #kills = []
    #killCount = 0
    #times = []
    #ticksElapsed = 0

    start_mission(agent_host, my_mission, my_mission_record) #start mission
    world_state = agent_host.getWorldState()
    i = 0
    while world_state.is_mission_running and i < DEFAULT_NUM_TRIALS:
    	#print "epsilon: {}".format(e)




        #start of SARSA
        s,ob = getState()
        #summon a mob in a random corner
        spawn_mob(findUs([EntityInfo(**k) for k in ob[u'entities']]))

        #a = (int(random.random() * 4) % 4)
        #agent_host.sendCommand("chat /summon {0} {1} {2} {3} {4}".format(MOB_TYPE, MOB_START_LOCATION[a][0], MOB_START_LOCATION[a][1], #MOB_START_LOCATION[a][2], "{IsBaby:0}")) #summon mob

        q_table = perform_trial(s, actions, do_action, isTerminal, q_table, epsilon = EPSILON, alpha=ALPHA, gamma=GAMMA)
        print "Trial {} finished.".format(i+1)
        world_state = agent_host.getWorldState()
        if world_state.is_mission_running:
            agent_host.sendCommand("quit")

        #get STATISTICS here
        #rewards.append(cumulative_reward)
        #kills.append(killCount)
        #times.append(ticksElapsed)
        #print "Cumulative reward: {}".format(cumulative_reward)
        #print "Kill count: {}".format(killCount)
        #print "Actions performed: {}".format(ticksElapsed)
        EPSILON *= EPSILON_DECAY
        pickle.dump(q_table, open(QTABLE_FILENAME, "wb"))
        for k,v in STATISTICS.items():
            print k + ":", v
            with open(k + STATISTICS_OUTPUT_FILE, "a") as f:
                f.write("{} ".format(v))
            STATISTICS[k] = 0
        #cumulative_reward = 0.
        #killCount = 0
        #ticksElapsed = 0
        start_mission(agent_host, my_mission, my_mission_record) #restart mission
        world_state = agent_host.getWorldState()
        i += 1
        #e *= 0.85



#    with open(STATISTICS_OUTPUT_FILE, 'w') as f:
#        for r in rewards:
#            f.write("{} ".format(r))
#        f.write("\n")
#        for k in kills:
#            f.write("{} ".format(k))
#        f.write("\n")
#        for t in times:
#        	f.write("{} ".format(t))

    # mission has ended.
    for error in world_state.errors:
        print "Error:", error.text

    print "Mission ended"
    # Mission has ended.
    time.sleep(1)  # Give mod some time.
