# Copyright (C) 2019-2022 Battelle Memorial Institute
# file: main.py

# This is a sample Python script.

# Press Shift+F10 to execute it or replace it with your code.
# Press Double Shift to search everywhere for classes, files, tool windows, actions, and settings.
import json
import sqlite3

from entity import assign_defaults
from entity import assign_item_defaults
from entity import Entity
from model import GLModel
from modifier import GLMModifier

from data import entities_path
from data import feeders_path


class mytest:
    def test(self):
        return


def test1():
    mylist = {}
    # entity_names = ["SimulationConfig", "BackboneFiles",  "WeatherPrep", "FeederGenerator",
    #             "EplusConfiguration", "PYPOWERConfiguration", "AgentPrep", "ThermostatSchedule"]
    # entity_names = ['house', 'inverter', 'battery', 'object solar', 'waterheater']

    try:
        conn = sqlite3.connect(entities_path + 'test.db')
        print("Opened database successfully")
    except:
        print("Database Sqlite3.db not formed")

    # this a config  -- file probably going to be static json
    file_name = 'feeder_defaults.json'
    myEntity = mytest()
    assign_defaults(myEntity, file_name)
    name = 'rgnPenResHeat'
    print(name)
    print(type(myEntity.__getattribute__(name)))
    print(myEntity.__getattribute__(name))

    # this a config  -- file probably going to be static
    myEntity = mytest()
    assign_item_defaults(myEntity, file_name)
    print(type(myEntity.__getattribute__(name)))
    print(myEntity.rgnPenResHeat.datatype)
    print(myEntity.rgnPenResHeat.item)
    print(myEntity.rgnPenResHeat)
    print(myEntity.rgnPenResHeat.value)

    # Better to use Entity as subclass like substation for metrics
    # Better to use Entity as object models for editing and persistence like glm_model,

    # this a multiple config file a using dictionary list persistence
    file_name = 'glm_objects.json'
    with open(entities_path + file_name, 'r', encoding='utf-8') as json_file:
        entities = json.load(json_file)
        mylist = {}
        for name in entities:
            mylist[name] = Entity(name, entities[name])
            print(mylist[name].toHelp())
            mylist[name].instanceToSQLite(conn)


def test2():
    # Test model.py
    model_file = GLModel()
    tval = model_file.read(feeders_path + "R1-12.47-1.glm")
    # Output json with new parameters
    model_file.write(entities_path + "test.glm")
    model_file.instancesToSQLite()

    print(model_file.entitiesToHelp())
    print(model_file.instancesToGLM())

    op = open(entities_path + 'glm_objects2.json', 'w', encoding='utf-8')
    json.dump(model_file.entitiesToJson(), op, ensure_ascii=False, indent=2)
    op.close()


def test3():
    modobject = GLMModifier()

    file_name = "R1-12.47-1.glm"
    tval = modobject.read_model(feeders_path + file_name)

    for name in modobject.model.entities:
        print(modobject.model.entities[name].toHelp())


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    # test1()
    test2()
    test3()
    