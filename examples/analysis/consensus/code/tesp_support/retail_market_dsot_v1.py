# Copyright (C) 2017-2019 Battelle Memorial Institute
# file: retail_market_dsot_v1.py 
"""Class that manages the operation of retail market at substation-level

Functionalities include: collecting curve bids from DER and DSO agents; 
generating aggregated buyer and seller curves; market clearing for both RT and DA
retail markets; deciding cleared quantity for individual DERs.   

The function call order for this agent is:
    
    initialize(retail_dict)
    
    Repeats at every hour:
        clean_bids_DA()
        curve_aggregator_DA(identity, bid, id)
        ...
        curve_aggregator_DA(identity, bid, id)
        clear_market_DA(transformer_degradation, Q_max)
        
        Repeats at every 5 min:
            clean_bids_RT()
            curve_aggregator_RT(identity, bid, id)
            ...
            curve_aggregator_RT(identity, bid, id)
            clear_market_RT(transformer_degradation, Q_max)

"""

import tesp_support.helpers_dsot_v1 as helpers 
from copy import deepcopy
import numpy as np
import logging as log


class RetailMarketDSOT:  
    """This agent manages the retail market operating

    Args:
        
        name (str): name of the retail market agent
        pricecap (float): the maximun price that is allowed in the market, in $/kWh
        num_samples (int): the number of sampling points, describles how precisely the curve is sampled
        windowLength (int): length of the planning horizon for the DA market, in hours
        Q_max (float): capacity of the substation, in kWh
        maxPuLoading (float): rate of the maxPuLoading for transformer
        
    Attributes:
        
        curve_buyer_RT (curve): aggregated buyer curve, updated after receiving each RT buyer bid
        curve_seller_RT (curve): aggregated seller curve, updated after receiving each RT seller bid
        curve_buyer_DA (dict of curves): 48 aggregated buyer curves, updated after receiving each DA buyer bid
        curve_seller_DA (dict of curves): 48 aggregated seller curves, updated after receiving each DA seller bid
        clear_type_RT (int): 0=uncongested, 1=congested, 2=inefficient, 3=failure
        clear_type_DA (list): list of clear type at each hour
        cleared_price_RT (float): cleared price for the substation for the next 5-min 
        cleared_price_DA (list): list of cleared price at each hour
        cleared_quantity_RT (float): cleared quantity for the substation for the next 5-min 
        cleared_quantity_DA (list): list of cleared quantity at each hour
        site_responsive_DA (list): Site_Day_Ahead quantity which is responsive
        site unresponsive DA (list): Site Day Ahead quantity which is unresponsive
        TOC_dict (dict): parameters related to transformer lifetime cost calculation, including
        OperatingPeriod (int): operating period, in minute
        timeStep (int): timestep, in minute
        Tamb (float): ambient temperature, in deg C
        delta_T_TO_init (int): initial delta temperature of top oil, in deg C
        delta_T_W_init (int): initial delta temperature of winding, in deg C
        BP (float): intial cost of transformer, in $
        toc_A (float): cost per watt for no-load losses, in $/W
        toc_B (float): cost per watt for load losses, in $/W
        Base_Year (float): expected lifespan of transformer, in year
        P_Rated (float): capacity, in W
        NLL_rate (float): no load loss rate, in %
        LL_rate (float): load loss rate, in %
        Sec_V (float): secondary voltage level, in volt
        TOU_TOR (float): oil time constant, in minute
        TOU_GR (float): winding time constant, in minute
        Oil_n (float): Oil exponent n
        Wind_m (float): Winding exponent m
        delta_T_TOR (float): top oil temperature rise, in deg C
        delta_T_ave_wind_R (float): average winding temperature rise over ambient temperature, in deg C
        AMES_RT (list X 5): Smooth Quadratics
        AMES_DA ((list X 5) X windowLength): Smooth Quadratics
    """

    def __init__(self, retail_dict, key):  
        """Initializes the class
        """
        self.name = key
        self.num_samples = retail_dict['num_samples']
        self.pricecap = retail_dict['pricecap']
        self.Q_max = retail_dict['Q_max']
        self.maxPuLoading = retail_dict['maxPuLoading']
        self.windowLength = retail_dict['windowLength']
        self.FeederCongCapacity = self.Q_max
        self.FeederPkDemandCapacity = self.Q_max 
        self.curve_buyer_RT = None
        self.curve_seller_RT = None
        self.curve_buyer_DA = dict()
        self.curve_seller_DA = dict()
        self.FeederCongPrice = 1e-7 # TODO: maybe initialize it for different feeders, with different values

        self.clear_type_RT = None
        self.clear_type_DA = []        
        self.cleared_price_RT = None
        self.cleared_price_DA = []
        self.cleared_quantity_RT = None
        self.cleared_quantity_DA = []
        self.congestion_surcharge_RT = None
        self.congestion_surcharge_DA = []
        # Industrial Load Parameters
        self.industrial_bid_da = []
        self.industrial_bid_rt = []
        self.industrial_load_elasticity = 5 # delP/delQ
        self.site_quantity_DA = dict()
        # substation transformer lifetime cost parameters
        self.TOC_dict = {
                'OperatingPeriod': retail_dict['OperatingPeriod'], 
                'timeStep': retail_dict['timeStep'], 
                'Tamb': retail_dict['Tamb'], 
                'delta_T_TO_init': retail_dict['delta_T_TO_init'],
                'delta_T_W_init': retail_dict['delta_T_W_init'],
                'BP': retail_dict['BP'], 
                'toc_A': retail_dict['toc_A'], 
                'toc_B': retail_dict['toc_B'],   
                'Base_Year': retail_dict['Base_Year'], 
                'P_Rated': retail_dict['P_Rated'],
                'NLL_rate': retail_dict['NLL_rate'], 
                'LL_rate': retail_dict['LL_rate'], 
                'Sec_V': retail_dict['Sec_V'], 
                'TOU_TOR': retail_dict['TOU_TOR'], 
                'TOU_GR': retail_dict['TOU_GR'], 
                'Oil_n': retail_dict['Oil_n'], 
                'Wind_m': retail_dict['Wind_m'], 
                'delta_T_TOR': retail_dict['delta_T_TOR'], 
                'delta_T_ave_wind_R': retail_dict['delta_T_ave_wind_R'], 
                }
        #AMES
        self.AMES_RT = None
        self.AMES_DA = None
        
    def clean_bids_RT(self):
        """Initialize the real-time market  
        """
        self.clear_type_RT = None
        self.cleared_price_RT = None
        self.cleared_quantity_RT = None
        self.curve_buyer_RT = None
        self.curve_seller_RT = None
        self.congestion_surcharge_RT = None

        self.curve_buyer_RT = helpers.curve(self.pricecap, self.num_samples)
        self.curve_seller_RT = helpers.curve(self.pricecap, self.num_samples)

    def clean_bids_DA(self):
        """Initialize the day-ahead market
        """  
        self.clear_type_DA = []
        self.cleared_price_DA = []
        self.cleared_quantity_DA = []
        self.congestion_surcharge_DA = []
        self.curve_buyer_DA = dict()
        self.curve_seller_DA = dict()
        
        for i in range(self.windowLength):
            self.curve_buyer_DA[i] = helpers.curve(self.pricecap, self.num_samples)

        for i in range(self.windowLength):
            self.curve_seller_DA[i] = helpers.curve(self.pricecap, self.num_samples)

    def curve_aggregator_RT(self, identity, bid_RT, id):
        """Function used to collect the RT bid and update the accumulated buyer or seller curve 
        
        Args:
            identity (str): identifies whether the bid is collected from a "Buyer" or "Seller"
            bid_RT (list): a nested list with dimension (m, 2), with m equals 2 to 4 
            id (str): name of the buyer or seller
        
        """
        if identity == 'Buyer':
            self.curve_buyer_RT.curve_aggregator(identity, bid_RT)
        else:
            self.curve_seller_RT.curve_aggregator(identity, bid_RT)

    def curve_aggregator_DA(self, identity, bid_DA, id):
        """Function used to collect the DA bid and update the accumulated buyer or seller curve  
        
        Args:
            identity (str): identifies whether the bid is collected from a "Buyer" or "Seller"
            bid_DA (list): a nested list with dimension (self.windowLength, m, 2), with m equals 2 to 4 
            id (str): name of the buyer or seller
        
        """
        if identity == 'Buyer':
            for i in range(self.windowLength):
                self.curve_buyer_DA[i].curve_aggregator(identity, bid_DA[i])
        else:
            for i in range(self.windowLength):
                self.curve_seller_DA[i].curve_aggregator(identity, bid_DA[i])

    def clear_market(self, curve_buyer, curve_seller, transformer_degradation, Q_max):
        """Shared function called by both clear_market_RT and clear_market_DA functions to find the intersection between supply curve and demand curve
        
        Args:
            curve_buyer (curve): aggregated buyer curve
            curve_seller (curve): aggregated seller curve
            transformer_degradation (boolean): equals to 1 if transformer_degradation is considered in the supply curve
            Q_max (float): substation capacity, in kWh
            
        Outputs:
            clear_type (int)
            cleared_price (float)
            cleared_quantity (float)
            congestion_surcharge (float)
        """
        cleared_price = 0.0
        cleared_quantity = 0.0
        
        if curve_buyer.uncontrollable_only == True:
            temp = curve_buyer.quantities[0] 
            if min(curve_seller.quantities) <= temp <= max(curve_seller.quantities):
                cleared_quantity = temp
                for idx in range(1, self.num_samples):
                    if curve_seller.quantities[idx-1] < cleared_quantity < curve_seller.quantities[idx]:
                        cleared_price = curve_seller.prices[idx-1]+(cleared_quantity-curve_seller.quantities[idx-1])*(curve_seller.prices[idx]-curve_seller.prices[idx-1])/(curve_seller.quantities[idx]-curve_seller.quantities[idx-1])
                    elif curve_seller.quantities[idx-1] == cleared_quantity:
                        cleared_price = curve_seller.prices[idx-1]
                    elif curve_seller.quantities[idx] == cleared_quantity:
                        cleared_price = curve_seller.prices[idx]
                # if cleared_quantity > self.Q_max:
                #     clear_type = helpers.ClearingType.CONGESTED
                # else:
                #     clear_type = helpers.ClearingType.UNCONGESTED
                if cleared_quantity > self.Q_max:
                    clear_type = helpers.ClearingType.CONGESTED
                    uncongested_price = cleared_price - (cleared_quantity-Q_max) * self.FeederCongPrice
                    if uncongested_price < 0:
                        uncongested_price = 0
                    congestion_surcharge = cleared_price - uncongested_price
                    if congestion_surcharge > self.pricecap:
                        congestion_surcharge = self.pricecap
                        log.info("congestion surchage is beyond pricecap, scale is too high!")
                else:
                    clear_type = helpers.ClearingType.UNCONGESTED
                    congestion_surcharge = 0.0
                return clear_type, cleared_price, cleared_quantity, congestion_surcharge
            else:
                return helpers.ClearingType.FAILURE, float('inf'), float('inf'), float('inf')
        else:
            max_q = min(max(curve_seller.quantities), max(curve_buyer.quantities))
            min_q = max(min(curve_seller.quantities), min(curve_buyer.quantities))
            if max_q < min_q:
                return  helpers.ClearingType.FAILURE, float('inf'), float('inf'), float('inf')

            buyer_quantities, buyer_prices = helpers.resample_curve(curve_buyer.quantities, curve_buyer.prices,
                                                                    min_q, max_q, self.num_samples)
            seller_quantities, seller_prices = helpers.resample_curve(curve_seller.quantities, curve_seller.prices,
                                                                      min_q, max_q, self.num_samples)
            for idx in range(len(buyer_quantities) - 1):
                if buyer_prices[idx] > seller_prices[idx] and buyer_prices[idx + 1] < seller_prices[idx + 1]:
                    p1 = (buyer_quantities[idx], buyer_prices[idx])
                    p2 = (buyer_quantities[idx + 1], buyer_prices[idx + 1])
                    p3 = (seller_quantities[idx], seller_prices[idx])
                    p4 = (seller_quantities[idx + 1], seller_prices[idx + 1])
                    cleared_price, cleared_quantity = helpers.get_intersect(p1, p2, p3, p4)
                    if cleared_quantity > self.Q_max:
                        clear_type = helpers.ClearingType.CONGESTED
                    else:
                        clear_type = helpers.ClearingType.UNCONGESTED
                    if cleared_quantity > Q_max:
                        clear_type = helpers.ClearingType.CONGESTED
                        uncongested_price = cleared_price - (cleared_quantity - Q_max) * self.FeederCongPrice
                        if uncongested_price < 0:
                            uncongested_price = 0
                        congestion_surcharge = cleared_price - uncongested_price
                        if congestion_surcharge > self.pricecap:
                            congestion_surcharge = self.pricecap
                            log.info("congestion surchage is beyond pricecap, scale is too high!")
                    else:
                        congestion_surcharge = 0.0
                    return clear_type, cleared_price, cleared_quantity, congestion_surcharge

            if buyer_prices[0] > seller_prices[0]:
                if max_q == max(curve_seller.quantities):
                    cleared_price = buyer_prices[-1]
                    cleared_quantity = buyer_quantities[-1]
                    clear_type = helpers.ClearingType.CONGESTED
                elif max_q == max(curve_buyer.quantities):
                    cleared_price = seller_prices[-1]
                    cleared_quantity = seller_quantities[-1]
                    clear_type = helpers.ClearingType.UNCONGESTED
            else:
                if min_q == min(curve_seller.quantities):
                    cleared_price = buyer_prices[0]
                    cleared_quantity = buyer_quantities[0]
                    clear_type = helpers.ClearingType.UNCONGESTED
                elif min_q == min(curve_buyer.quantities):
                    cleared_price = seller_prices[0]
                    cleared_quantity = seller_quantities[0]
                    clear_type = helpers.ClearingType.UNCONGESTED
            if cleared_quantity > Q_max:
                clear_type = helpers.ClearingType.CONGESTED
                uncongested_price = cleared_price - (cleared_quantity - Q_max) * self.FeederCongPrice
                if uncongested_price < 0:
                    uncongested_price = 0
                congestion_surcharge = cleared_price - uncongested_price
                if congestion_surcharge > self.pricecap:
                    congestion_surcharge = self.pricecap
                    log.info("congestion surchage is beyond pricecap, scale is too high!")
            else:
                congestion_surcharge = 0.0
            return clear_type, cleared_price, cleared_quantity, congestion_surcharge
        
    def clear_market_RT(self, transformer_degradation, Q_max):
        """Function used for clearing the RT market
        
        Three steps of work are fullfilled in this function: First the buyer curve is fitted polynomial;
        Second clear_market function is called for calculating the cleared price and cleared quantity for the whole market; 
        Third distribute_cleared_quantity function is called for finding the cleared price and cleared quantity for the individual DERs.
        
        buyer_info_RT and seller_info_RT, clear_type_RT, cleared_price_RT and cleared_quantity_RT are updated with cleared results
    
        """
        self.clear_type_RT, self.cleared_price_RT, self.cleared_quantity_RT, self.congestion_surcharge_RT = \
            self.clear_market(self.curve_buyer_RT, self.curve_seller_RT, transformer_degradation, Q_max)

    def clear_market_DA(self, transformer_degradation, Q_max):
        """Function used for clearing the DA market
        
        Three steps of work are fullfilled in a loop in this function: First the buyer curve at each hour is fitted polynomial;
        Second clear_market function is called for calculating the cleared price and cleared quantity for the whole market at each hour; 
        Third distribute_cleared_quantity function is called for finding the cleared price and cleared quantity for the individual DERs at each hour.
        
        buyer_info_DA and seller_info_DA, clear_type_DA, cleared_price_DA and cleared_quantity_DA are updated with cleared results        
        
        """

        for i in range(self.windowLength):
            clear_type, cleared_price, cleared_quantity, congestion_surcharge = \
                self.clear_market(self.curve_buyer_DA[i], self.curve_seller_DA[i], transformer_degradation, Q_max)
            self.clear_type_DA.append(clear_type)
            self.cleared_price_DA.append(cleared_price)
            self.cleared_quantity_DA.append(cleared_quantity)
            self.congestion_surcharge_DA.append(congestion_surcharge)

    def test_function(self):
        """ Test function with the only purpose of returning the name of the object

        """
        return self.name

    ########################################################################### AMES
    def curve_aggregator_AMES_RT(self, demand_curve_RT, Q_max, Q_cleared, price_forecast):
        """ Function used to aggregate the substation-level RT demand curves into a DSO-level RT demand curve
        
        Args:
            demand_curve_RT (curve): demand curve to be aggregated for real-time
            Q_max (float): maximum capacity of the substation, in kW
            Q_cleared (float): locally cleared quantity of the substation in kW
            price_forecast (float): locally forecast price at the substation level

        """
#        log.info("runing curve_aggregator_AMES_RT")
        self.AMES_RT = list()
        substation_curve = deepcopy(self.curve_preprocess(demand_curve_RT, Q_max))
#        print(
#            "RT [min, max] substation curve: [" + str(min(substation_curve.quantities)) + ", " + str(
#                min(substation_curve.quantities)) + "]")

        if (min(substation_curve.quantities) == max(substation_curve.quantities)):
            for i in range(self.num_samples):
                substation_curve.quantities[i] = Q_cleared
#            print(
#            "RT [min, max] substation curve: [" + str(min(substation_curve.quantities)) + ", " + str(
#                min(substation_curve.quantities)) + "]")
        self.AMES_RT = self.convert_2_AMES_quadratic_BID(substation_curve, Q_cleared, price_forecast)

        log.info("-- AMES RT conversion: --> demand bid [min,max] = [ " + str(min(substation_curve.quantities)) + ", " + str(Q_cleared/1000.0) + " ]")
        log.info("-- AMES RT conversion: --> AMES bid [unresp_mw, respmax_mw] = [ " + str(self.AMES_RT[0]) + ", " + str(self.AMES_RT[1]) + " ]")

    def curve_aggregator_AMES_DA(self, demand_curve_DA, Q_max, Q_cleared, price_forecast):
        """ Function used to aggregate the substation-level DA demand curves into a DSO-level DA demand curve  
        
        Args:
            demand_curve_DA (dict): a collection of demand curves to be aggregated for day-ahead
            Q_max (float): maximum capacity of the substation, in kW
            Q_cleared (float): locally cleared quantity of the substation in kW
            price_forecast (float): locally forecast price at the substation level
        """
#        log.info("runing curve_aggregator_AMES_DA")
        self.AMES_DA = list()
        for idx in range(self.windowLength):
            substation_curve = deepcopy(self.curve_preprocess(demand_curve_DA[idx], Q_max))
            # since we plan to now not use the ./run.sh base, we need to make sure that the difference between substation curve
            # resizing is not taken as the flexibility
#            print("Hour " + str(idx) + " Cleared Quantity " + str(Q_cleared[idx]))

#            print("Before Adjusting hour " + str(idx)+ " [min, max] substation curve: [" + str(min(substation_curve.quantities)) + ", " + str(max(substation_curve.quantities)) + "]")
            if (min(substation_curve.quantities) == max(substation_curve.quantities)):
                for i in range(self.num_samples):
                        substation_curve.quantities[i] = Q_cleared[idx]

#            print("After Adjusting hour " + str(idx) + " [min, max] substation curve: [" + str(min(substation_curve.quantities)) + ", " + str(max(substation_curve.quantities)) + "]")

            self.AMES_DA.append(self.convert_2_AMES_quadratic_BID(substation_curve, Q_cleared[idx], price_forecast[idx]))

            log.info("-- AMES DA conversion hour: " + str(idx) + "--> demand bid [min,max] = [ " + str(min(substation_curve.quantities)) + ", " + str(Q_cleared[idx]/1000.0) + " ]")
            log.info("-- AMES DA conversion hour: " + str(idx) + "--> AMES bid [unresp_mw, respmax_mw] = [ " + str(self.AMES_DA[idx][0]) + ", " + str(self.AMES_DA[idx][1]) + " ]")

    def curve_preprocess(self, substation_demand_curve, Q_max):
        """ An internal shared function called by curve_aggregator_DSO_RT and curve_aggregator_DSO_DA functions to truncate 
            the substation demand curve before aggregation as well as convert the retail prices into wholesale prices
        
        Args:
            substation_demand_curve (curve): substation demand curve to be preprocessed
            Q_max (float): maximum capacity of the substation, in kW
            
        Return:
            preprocessed_curve (curve): preprossed demand curve
        
        """
        preprocessed_curve = helpers.curve(self.pricecap, self.num_samples)
        preprocessed_curve.prices = deepcopy(substation_demand_curve.prices)
        preprocessed_curve.quantities = deepcopy(substation_demand_curve.quantities)
        for i in range(self.num_samples): 
            # Truncate the substation-level demand curve by maximun capacity of the substation
            if preprocessed_curve.quantities[i] >= Q_max:        
                preprocessed_curve.quantities[i] = Q_max
            # #  we are much lower than what TSO is expecting from us, with this low quantity AMES won't clear market as there can be high wind values
            # elif (Q_max - preprocessed_curve.quantities[i]  > 1000):
            #     # increase the quantity to match what's TSO is expecting
            #     preprocessed_curve.quantities[i] = preprocessed_curve.quantities[i] \
            #                                         + (Q_max - preprocessed_curve.quantities[i])

            # Convert the retail price into wholesale price   
            preprocessed_curve.prices[i] = self.retail_rate_inverse(preprocessed_curve.prices[i])
        return preprocessed_curve  
    
    def retail_rate_inverse(self, Pr):
        """ Function used to convert the retail prices into wholesale prices
        
        Args:
            Pr (float): retail price, in $/kWh
                
        Return: 
            Pw (float): wholesale price, in $/kWh
        """
        Pw = deepcopy(Pr/1.25)
        return Pw
    
    def convert_2_AMES_quadratic_BID(self, curve, Q_cleared, price_forecast):
        """ Convert aggregated DSO type bid to AMES quadratic curve
        
        Args:
            curve (curve): substation demand curve to be preprocessed
            price_forecast: locally forecast price at the substation level
            Q_cleared: locally cleared quantity at the substation
        Return:
            quadractive_bid (list): f(Q)=resp_c2*Q^2+C1*Q+C0
                unresp_mw (float): minimun demand MW
                resp_max_mw (float): maximun demand MW
                resp_c2 (float): quadratic coefficient 
                resp_c1 (float): linear coefficient
                resp_c0 (float): constant coefficient
                resp_deg (int): equal to "2" to represent the current order in the list
        """
#        log.info("runing convert_2_AMES_quadratic_BID")
        # All substation quantities are in kW, converting them to MW and $/MWh to be used for AMES
        curve.quantities = curve.quantities/1000
        Q_cleared = Q_cleared / 1000.0
        curve.prices = curve.prices*1000
        price_forecast = price_forecast*1000
        try:
            if self.basecase:
                # unresp_mw = np.amin(curve.quantities/2)
                # resp_max_mw = np.amax(curve.quantities/2)
                unresp_mw = Q_cleared
                resp_max_mw = Q_cleared
            else:
                unresp_mw = np.amin(curve.quantities)
                #resp_max_mw = np.amax(curve.quantities)
                resp_max_mw = Q_cleared
                if resp_max_mw < unresp_mw: # due to correcting of the real-time bid and then preprocessing it the cleared quantity is less than the minimum demand bid
                    resp_max_mw =  np.amax(curve.quantities)
            if unresp_mw == resp_max_mw:
                Q_range = np.array([])
                P_range = np.array([])
            else:
                benefit = curve.quantities*price_forecast # we have to make benefit function curve, not the $/MWh bid.
                new_Q, new_benefit = helpers.resample_curve(curve.quantities, benefit, unresp_mw, resp_max_mw, self.num_samples)
                new_benefit = np.array(new_benefit)
                inde = np.where((new_Q>(unresp_mw)) & (new_Q<resp_max_mw))
                Q_range = new_Q[inde]
                new_benefit = new_benefit[inde]


        except Exception as e:
            log.info("-- Error on inputs for bid conversion to AMES --")
            log.info(e)
            unresp_mw = 0.0
            resp_max_mw = 0.0
            Q_range = np.array([])
            P_range = np.array([])
        
        if Q_range.size == 0:
            if unresp_mw == resp_max_mw:
                log.info("-- No DERs bids convert_2_AMES_quadratic_BID --")
                z = list([-0.0, 0.0, 0.0])
            else:
                log.info("-- Error empty array for fitting curve for AMES --")
                log.info("quantity")
                log.info(str(curve.quantities))
                log.info("prices:")
                log.info(str(curve.prices))
                z = list([-0.0, 0.0, 0.0])
        else:
            z = np.polyfit(x=Q_range, y=new_benefit, deg=2)

            if abs(z[0]) < 1e-3:  # This means the curve is almost linear and due to the resolution of piecewise linear benefit function it won't be able to make the piecewise bids
                z[0] = -z[1] / (2 * ( (resp_max_mw-unresp_mw) ))
            # check for concavity, which is a necessary condition for AMES to converge with Price Sensitivity Load
            if z[1] - z[0]*(resp_max_mw-unresp_mw) < 0:
                try:
                    resp_max_mw = unresp_mw + abs(z[1]/z[0])
                except:
                    print('quadratic coefficient of price sensitivity bid to AMES found zero for bid of quantity range'+str([unresp_mw, resp_max_mw]))
                    resp_max_mw = unresp_mw
                    z[0] = 0
                    z[1] = 0
                    z[2] = 0


        resp_deg = 2
        # below conversion is because fncsTSO is written in terms of MWs and the DSO is in kWs
        bid = list()
        # bid.append(unresp_mw/1000.0)
        # bid.append(resp_max_mw/1000.0)
        # bid.append(-z[0]* 1000.0 * 1000.0) #c2  ---> f in the manual, -negative sign makes it strictly concave with which the check has been added above
        # bid.append(z[1]* 1000.0) #c1 ----> e in the manual
        # bid.append(z[2]) #c0 ----> d in the manual
        # bid.append(resp_deg)
        bid.append(unresp_mw)
        bid.append(resp_max_mw-unresp_mw)
        bid.append(z[0]) #c2  ---> f in the manual, -negative sign makes it strictly concave with which the check has been added above
        bid.append(z[1]) #c1 ----> e in the manual
        bid.append(z[2]) #c0 ----> d in the manual
        bid.append(resp_deg)

        return bid

    def formulate_bid_industrial_rt(self, industrial_load, price_cleared):
        """This file is to create real time bid curve for industrial customer for the given demand-price elasticity
            elasticity (a) = (del-Q/Q)/(del-P/P) --> del-Q/del-p = a * Q / P
            The slope of the bid curve for the industrial load will be Slope (S) =  (del-Q/del-P)
            The Q-axis intercept for the bid curve will be (C_Q) = Q + S * del-p = Q - a * Q
            The P-axis intercept for the bid curve will be (C_P) = P + S * 1 / del-Q = P -  P / a


        Args:
            price_cleared (float): Price cleared in $/kWh at the instance (if we are going to bid directly in ISO, then this is LMP forecast, or else, it is retail price forecast
            industrial_load (float): The most updated (closer to real-time) industrial load in kW at that instant

        Return:
            bid_rt (1,2)X4): 4 point industrial load bid
        """

        PT_1 = price_cleared - price_cleared / self.industrial_load_elasticity  # P-axis intercept
        QT_1 = 0

        PT_2 = price_cleared
        QT_2 = industrial_load

        PT_3 = price_cleared
        QT_3 = industrial_load

        PT_4 = 0
        QT_4 = industrial_load - self.industrial_load_elasticity * industrial_load

        self.industrial_bid_rt = [[PT_1, QT_1], [PT_2, QT_2], [PT_3, QT_3], [PT_4, QT_4]]
        return self.industrial_bid_rt

    def formulate_bid_industrial_da(self, industrial_load_forecast, price_forecast):
        """This file is to create a bid curve for industrial customer for the given demand-price elasticity
            elasticity (a) = (del-Q/Q)/(del-P/P) --> del-Q/del-p = a * Q / P
            The slope of the bid curve for the industrial load will be Slope (S) =  (del-Q/del-P)
            The Q-axis intercept for the bid curve will be (C_Q) = Q + S * del-p = Q - a * Q
            The P-axis intercept for the bid curve will be (C_P) = P + S * 1 / del-Q = P -  P / a


        Args:
            price_forecast: forecasted price in $/kWh at the instance (if we are going to bid directly in ISO, then this is LMP forecast, or else, it is retail price forecast
            industrial_load_forecast: forecast quantity of industrial load in kW at that instant

        Return:
            bid_da (float) (1 x windowLength): Bid quantity from optimization for all hours of the window specified by windowLength
        """
        TIME = range(0, self.windowLength)

        BID = [[[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]] for i in TIME]

        for t in TIME:
            PT_1 = price_forecast[t] - price_forecast[t] / self.industrial_load_elasticity  # P-axis intercept
            QT_1 = 0

            PT_2 = price_forecast[t]
            QT_2 = industrial_load_forecast[t]

            PT_3 = price_forecast[t]
            QT_3 = industrial_load_forecast[t]

            PT_4 = 0
            QT_4 = industrial_load_forecast[t] - self.industrial_load_elasticity * industrial_load_forecast[t]
            BID[t][0][0] = QT_1
            BID[t][1][0] = QT_2
            BID[t][2][0] = QT_3
            BID[t][3][0] = QT_4

            BID[t][0][1] = PT_1
            BID[t][1][1] = PT_2
            BID[t][2][1] = PT_3
            BID[t][3][1] = PT_4

        self.industrial_bid_da = deepcopy(BID)
        return self.industrial_bid_da

    def process_site_da_quantities(self, forecast_load, name, status):
        """Function stores the day-ahead quantities, primarily for HVAC at the moment, it utilizes

        arguments in:
        unresponsive loads (1x 48) dataframe
        responsive loads (1x48) dataframe
        name: of the house  (str)
        returns
        self.site_quantity_DA (dict, 'name', ' status (participating or not participating',  'Quantity (1 x 48) )
        """
        self.site_quantity_DA['name'] = name
        self.site_quantity_DA['status'] = status
        self.site_quantity_DA['values'] = forecast_load.tolist()
        return self.site_quantity_DA


        
if __name__ == "__main__":
    """ Testing AMES
    """
    import matplotlib.pyplot as plt
    agent={"unit": "kW", "pricecap": 1.0, "num_samples": 100, "windowLength": 48, "Q_max": 3310000, "maxPuLoading": 1.5, "period_da": 3600, "period_rt": 300, "OperatingPeriod": 1440, "timeStep": 1, "Tamb": 30, "delta_T_TO_init": 25, "delta_T_W_init": 25, "BP": 100000, "toc_A": 1, "toc_B": 1, "Base_Year": 20.54794520547945, "P_Rated": 2500000.0, "NLL_rate": 0.3, "LL_rate": 1.0, "Sec_V": 69000, "TOU_TOR": 75.0, "TOU_GR": 5, "Oil_n": 0.8, "Wind_m": 0.8, "delta_T_TOR": 55, "delta_T_ave_wind_R": 65, "distribution_charge_rate": 0.04}
    market = RetailMarketDSOT(agent,'test')
    market.clean_bids_DA()
    market.basecase = False
    # Retail agent collects DA bids from DERs
    buyer_bid_1 = [[[5.0, 0.19751947247066967], [10, 0.1505365976142517], [10, 0.11320393929262458], [15.0, 0.06622106443620661]],
                   [[0.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [0.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]]]
    market.curve_aggregator_DA('Buyer', buyer_bid_1, 'abc')
 
    buyer_bid_2 = [[[5.0, 0.19751947247066967], [10, 0.1505365976142517], [10, 0.11320393929262458], [15.0, 0.06622106443620661]],
                   [[0.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [0.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]]]
    market.curve_aggregator_DA('Buyer', buyer_bid_2, 'def')
    
    buyer_bid_3 = [[[5.0, 0.19751947247066967], [10, 0.1505365976142517], [10, 0.11320393929262458], [15.0, 0.06622106443620661]],
                   [[0.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [0.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]]]
    market.curve_aggregator_DA('Buyer', buyer_bid_3, 'ghi')
    
    buyer_bid_4 = [[[5.0, 0.19751947247066967], [10, 0.1505365976142517], [10, 0.11320393929262458], [15.0, 0.06622106443620661]],
                   [[0.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [0.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]]]
    market.curve_aggregator_DA('Buyer', buyer_bid_4, 'jkl')
    
    buyer_bid_5 = [[[5.0, 0.19751947247066967], [10, 0.1505365976142517], [10, 0.11320393929262458], [15.0, 0.06622106443620661]],
                   [[0.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [0.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]],
                   [[-5.0, 0.19751947247066967], [0, 0.1505365976142517], [0, 0.11320393929262458], [50.0, 0.06622106443620661]]]
    market.curve_aggregator_DA('Buyer', buyer_bid_5, 'mno')
    price_forecast = np.array([0.43, 0.41, 0.40, 0.39, 0.39, 0.40, 0.45, 0.45, 0.55, 0.80, 0.90, 0.98,
                       0.99, 1.00, 1.00, 0.99, 0.98, 0.97, 0.97, 0.90, 0.70, 0.60, 0.55, 0.45,
                       0.43, 0.41, 0.40, 0.39, 0.39, 0.40, 0.45, 0.45, 0.55, 0.80, 0.90, 0.98,
                       0.99, 1.00, 1.00, 0.99, 0.98, 0.97, 0.97, 0.90, 0.70, 0.60, 0.55, 0.45])/2
    for idx in range(market.windowLength):
        market.cleared_quantity_DA.append(max(market.curve_buyer_DA[idx].quantities)/2)

    market.curve_aggregator_AMES_DA(market.curve_buyer_DA, market.Q_max, market.cleared_quantity_DA, price_forecast)

    hr = 0

    unresp_mw = np.amin(market.curve_buyer_DA[hr].quantities)
    resp_max_mw = np.amax(market.curve_buyer_DA[hr].quantities)
    resp_deg = 2
    
    #    #with bids
    old_Q = market.curve_buyer_DA[hr].quantities
    old_P = market.curve_buyer_DA[hr].prices
#    old_P = old_P/1.25
    new_Q, new_P = helpers.resample_curve(old_Q, old_P, unresp_mw, resp_max_mw, market.num_samples)
    
    new_P = np.array(new_P)
    
    inde = np.where((new_Q>unresp_mw) & (new_Q<resp_max_mw))
    Q_range = (new_Q[inde] - unresp_mw)/1000.0 #to Mw
    P_range = (new_P[inde])/1.25 #to DSO level
    Benefit_range = Q_range*P_range

#    print("For the plot to be acurate a line must be uncommented in convert_2_AMES_quadratic_BID function")
    y=list()
    y2=list()

    for i in Q_range:
        y.append(market.AMES_DA[hr][2]*(i**2) + market.AMES_DA[hr][3]*i + market.AMES_DA[hr][4] )

    # new_Q_range = np.linspace(market.AMES_DA[hr][0], market.AMES_DA[hr][1], 100)
    # for i in new_Q_range:
    #     y2.append(market.AMES_DA[hr][2]*(i**2) + market.AMES_DA[hr][3]*i + market.AMES_DA[hr][4])
    #

    fig, ax = plt.subplots(2,1, figsize=(8,8))
#    ax[0].plot(Q_range, P_range, label='Aggregate demand curve',marker='x')
    ax[0].plot(new_Q/1000, 1000*new_P/1.25, label='New Aggregate demand curve',marker='x')
    ax[0].plot(old_Q/1000, 1000*old_P/1.25, label='Old Aggregate demand curve',marker='x')
    ax[0].set_ylabel('Price ($/MWh)')
    ax[0].set_xlabel('Qantity (MWh)')
    ax[0].legend()
    ax[1].plot(Q_range, y, label='Fitted for AMES - with Strict Concavity - e - f.P ='+str(market.AMES_DA[hr][3] - market.AMES_DA[hr][2]*market.AMES_DA[hr][1])+' >= 0')
#    ax[1].plot(Q_range, Benefit_range, label='Piecewise Benefit Curve',marker='x')
    ax[1].set_ylabel('Benefit ($/h)')
    ax[1].set_xlabel('Quantity (MWh)')
    ax[1].legend()
    plt.show()
    

#    
#    unresp_mw = np.amin(market.curve_buyer_DA[1].quantities)
#    resp_max_mw = np.amax(market.curve_buyer_DA[1].quantities)
#    #no bids
#    old_Q = market.curve_buyer_DA[1].quantities
#    old_P = market.curve_buyer_DA[1].prices
#    new_Q, new_P = helpers.resample_curve(old_Q, old_P, unresp_mw, resp_max_mw, market.num_samples)

    
    
    
    
    
    
    