
import pypower.api as pp
import tesp_support.tso_helpers as tso

ppc = tso.load_json_case('pp_8BusTestCase5000.json')

ppopt_market = pp.ppoption(VERBOSE=0, OUT_ALL=0, PF_DC=ppc['opf_dc'])
ppopt_regular = pp.ppoption(VERBOSE=0, OUT_ALL=0, PF_DC=ppc['pf_dc'])

rpf = pp.runpf(ppc, ppopt_regular)
res = pp.runopf(ppc, ppopt_market)

tso.summarize_opf(res)
