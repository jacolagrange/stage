import matplotlib
import matplotlib.pyplot as plt
from matplotlib.pyplot import title, xlabel, ylabel
from matplotlib.ticker import PercentFormatter
from matplotlib.patches import Patch
import seaborn as sns
import itertools

#Width of a column is 252 pt
column_figsize=(3.5, 1.08) #in inches
page_figsize=(7, 1.08) #in inches
fontsize = 8
legendfontsize = 5
axfontsize = 7
xlabelsize = 7
ylabelsize = 7
tick_padding = 0
fontpath = "/home/jaime/.local/share/fonts/OPTITimes-Roman.otf"
fontname = "OPTITimes-Roman"
y_minor_ndivs = None #default

handleheight = 0.7
handlelength = handleheight

#https://stackoverflow.com/questions/3899980/how-to-change-the-font-size-on-a-matplotlib-plot
custom_rcparams = {
    'text.usetex': True,
    'font.size': fontsize,
    'figure.titlesize': fontsize,
    'axes.labelsize': axfontsize,
    'xtick.labelsize': xlabelsize,
    'xtick.major.pad': tick_padding,
    'ytick.labelsize': ylabelsize,
    'ytick.major.pad': tick_padding,
    'legend.fontsize': legendfontsize,
    'legend.handleheight': handleheight,
    'legend.handlelength': handlelength,
    'patch.linewidth': 0.5,
    'legend.frameon': False,
    'axes.grid.which': "both",
}
# print(matplotlib.rcParams.keys())

matplotlib.font_manager.fontManager.addfont(fontpath)
sns.set(context="paper", font=fontname, style="whitegrid")

matplotlib.rcParams.update(custom_rcparams)

# Cannot update this at runtime -> Need a stylesheet for this https://matplotlib.org/stable/users/explain/customizing.html#customizing-with-dynamic-rc-settings
# matplotlib.set_minor_locator(matplotlib.ticker.AutoMinorLocator())
# matplotlib.grid(which="minor", axis="y", linestyle='--')
color_palette = sns.color_palette()
pale_palette = [sns.set_hls_values(color, l=0.7) for color in color_palette]

def flip(items, ncol):
        return itertools.chain(*[items[i::ncol] for i in range(ncol)])

# sns_ax.yaxis.set_minor_locator(matplotlib.ticker.MultipleLocator(1))
# print(sns_ax.yaxis.get_minorticklocs())

