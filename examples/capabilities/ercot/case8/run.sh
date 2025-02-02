#!/bin/bash

# Copyright (C) 2021-2022 Battelle Memorial Institute
# file: run.sh

mkdir -p PyomoTempFiles

(export FNCS_BROKER="tcp://*:5570" && exec fncs_broker 12 &> broker.log &)
(export WEATHER_CONFIG=weatherIAH.json && export FNCS_FATAL=YES && export FNCS_LOG_STDOUT=yes && exec python3 -c "import tesp_support.weatherAgent as tesp;tesp.startWeatherAgent('weatherIAH.dat')"  &> weatherIAH.log &)
(export WEATHER_CONFIG=weatherSPS.json && export FNCS_FATAL=YES && export FNCS_LOG_STDOUT=yes && exec python3 -c "import tesp_support.weatherAgent as tesp;tesp.startWeatherAgent('weatherSPS.dat')"  &> weatherSPS.log &)
(export WEATHER_CONFIG=weatherELP.json && export FNCS_FATAL=YES && export FNCS_LOG_STDOUT=yes && exec python3 -c "import tesp_support.weatherAgent as tesp;tesp.startWeatherAgent('weatherELP.dat')"  &> weatherELP.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus1_metrics.json Bus1.glm &> Bus1.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus2_metrics.json Bus2.glm &> Bus2.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus3_metrics.json Bus3.glm &> Bus3.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus4_metrics.json Bus4.glm &> Bus4.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus5_metrics.json Bus5.glm &> Bus5.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus6_metrics.json Bus6.glm &> Bus6.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus7_metrics.json Bus7.glm &> Bus7.log &)
(export FNCS_FATAL=YES && exec gridlabd -D USE_FNCS -D METRICS_FILE=Bus8_metrics.json Bus8.glm &> Bus8.log &)
(export FNCS_CONFIG_FILE=tso8.yaml && export FNCS_FATAL=YES && export FNCS_LOG_STDOUT=yes && exec python3 fncsTSO.py &> bulk.log &)
