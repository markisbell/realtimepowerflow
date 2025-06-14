# realtimepowerflow
This repo is focused on realtime simulation environments for scalable power flow simulations. By seperating the power grid into segments which each can be run in a docker container, simulations can be scaled horizontally. The subsystems interact with each other using Kafka real time meassaging, so that updates on bus voltages, currents in lines and the respective loads that are applied locally can be taken into consideration in each subsystem. The point of coupling between the subsystems must be defined in the configuration.

THE SYSTEM IS CURRENTLY COMPLETLY UNTESTED
