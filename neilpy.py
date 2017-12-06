# -*- coding: utf-8 -*-
"""
Created on Fri Dec  1 21:40:01 2017

@author: Thomas Pingel
"""

#%%
import struct
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import rasterio
import scipy.ndimage as ndi
from scipy import stats
from scipy import sparse
from scipy import linalg
from scipy.signal import convolve2d
from scipy import interpolate

#%%

with rasterio.open('sample_dem.tif') as src:
    Z = src.read(1)
    Zt = src.transform
    
#%% Raster visualization functions
    
# http://edndoc.esri.com/arcobjects/9.2/net/shared/geoprocessing/spatial_analyst_tools/how_hillshade_works.htm
def esri_slope(Z,cellsize=1,z_factor=1,return_as='degrees'):    
    def slope_filter(n):
        n = n.reshape((3,3)) # Added to accommodate filter, not strictly necessary.
        # Plus, this would be better if it factored in nans better.
        dz_dx = (np.sum(n[:,-1] * (1,2,1)) - np.sum(n[:,0] * (1,2,1))) / 8
        dz_dy = (np.sum(n[-1,:] * (1,2,1)) - np.sum(n[0,:] * (1,2,1))) / 8
        return np.sqrt(dz_dx**2 + dz_dy**2)
        
    S = ndi.filters.generic_filter(Z,slope_filter,size=3,mode='reflect')
    if cellsize != 1:
        S = S / cellsize
    if z_factor != 1:
        S = z_factor * S
    if return_as=='degrees':
        S = np.rad2deg(np.arctan(S))
    return S
        

def slope(Z,cellsize=1,z_factor=1,return_as='degrees'):
    if return_as not in ['degrees','radians','percent']:
        print('return_as',return_as,'is not supported.')
    else:
        gy,gx = np.gradient(Z,cellsize/z_factor)
        S = np.sqrt(gx**2 + gy**2)
        if return_as=='degrees' or return_as=='radians':
            S = np.arctan(S)
            if return_as=='degrees':
                S = np.rad2deg(S)
    return S

        

def aspect(Z,return_as='degrees',flat_as='nan'):
    if return_as not in ['degrees','radians']:
        print('return_as',return_as,'is not supported.')
    else:
        gy,gx = np.gradient(Z)
        A = np.arctan2(gy,-gx) 
        A = np.pi/2 - A
        A[A<0] = A[A<0] + 2*np.pi
        if return_as=='degrees':
            A = np.rad2deg(A)
        if flat_as == 'nan':
            flat_as = np.nan
        A[(gx==0) & (gy==0)] = flat_as
        return A

# http://edndoc.esri.com/arcobjects/9.2/net/shared/geoprocessing/spatial_analyst_tools/how_hillshade_works.htm
def hillshade(Z,cellsize=1,z_factor=1,zenith=45,azimuth=315):
    zenith, azimuth = np.deg2rad((zenith,azimuth))
    S = slope(Z,cellsize=cellsize,z_factor=z_factor,return_as='radians')
    A = aspect(Z,return_as='radians',flat_as=0)
    H = (np.cos(zenith) * np.cos(S)) + (np.sin(zenith) * np.sin(S) * np.cos(azimuth - A))
    H[H<0] = 0
    H = 255 * H
    H = np.round(H).astype(np.uint8)
    return H

# This could still use some work.
def multiple_illumination(Z,cellsize=1,z_factor=1,zeniths=np.array([45]),azimuths=4):
    if np.isscalar(azimuths):
        azimuths = np.arange(0,360,360/azimuths)
    if np.isscalar(zeniths):
        zeniths = 90 / (zeniths + 1)
        zeniths = np.arange(zeniths,90,zeniths)
    H = np.zeros(np.shape(Z))
    for zenith in zeniths:
        for azimuth in azimuths:
            H1 = hillshade(Z,cellsize=cellsize,z_factor=z_factor,zenith=zenith,azimuth=azimuth)
            H = np.stack((H,H1),axis=2)
            H = np.max(H,axis=2)
    return H

def pssm(Z,cellsize=1,ve=2.3,reverse=False):
    P = slope(Z,cellsize=cellsize,return_as='percent')
    P = np.rad2deg(np.arctan(2.3 *  P))
    P = (P - P.min()) / (P.max() - P.min())
    P = np.round(255*P).astype(np.uint8)
    if reverse==False:
        P = plt.cm.bone_r(P)
    else:
        P = plt.cm.bone(P)
    return P

def z_factor(latitude):
    # https://blogs.esri.com/esri/arcgis/2007/06/12/setting-the-z-factor-parameter-correctly/
    latitude = np.deg2rad(latitude)
    m=6367449;
    a=6378137;
    b=6356752.3;
    numer=(a**4)*(np.cos(latitude)**2) + (b**4)*(np.sin(latitude)**2);
    denom=(a*np.cos(latitude))**2 + (b*np.sin(latitude))**2;
    z_factor = 1 / (np.pi / 180 * np.cos(latitude) * np.sqrt(numer/denom))
    return z_factor



#%% Lidar routines
"""
References:
http://stackoverflow.com/questions/16573089/reading-binary-data-into-pandas
https://docs.scipy.org/doc/numpy/reference/arrays.dtypes.html
LAZ http://howardbutler.com/javascript-laz-implementation.html
"""

# Reads a file into pandas dataframe
# Originally developed as research/current/lidar/bonemap
# A pure python las reader
def read_las(filename):

    with open(filename,mode='rb') as file:
        data = file.read()
    
    point_data_format_key = {0:20,1:28,2:26,3:34,4:57,5:63}
    
    # Read header into a dictionary
    header = {}
    header['file_signature'] = struct.unpack('<4s',data[0:4])[0].decode('utf-8')
    header['file_source_id'] = struct.unpack('<H',data[4:6])[0]
    header['global_encoding'] = struct.unpack('<H',data[6:8])[0]
    project_id = []
    project_id.append(struct.unpack('<L',data[8:12])[0])
    project_id.append(struct.unpack('<H',data[12:14])[0])
    project_id.append(struct.unpack('<H',data[14:16])[0])
    #Fix
    #project_id.append(struct.unpack('8s',data[16:24])[0].decode('utf-8').rstrip('\x00'))
    header['project_id'] = project_id
    del project_id
    header['version_major'] = struct.unpack('<B',data[24:25])[0]
    header['version_minor'] = struct.unpack('<B',data[25:26])[0]
    header['version'] = header['version_major'] + header['version_minor']/10
    header['system_id'] = struct.unpack('32s',data[26:58])[0].decode('utf-8').rstrip('\x00')
    header['generating_software'] = struct.unpack('32s',data[58:90])[0].decode('utf-8').rstrip('\x00')
    header['file_creation_day'] = struct.unpack('H',data[90:92])[0]
    header['file_creation_year'] = struct.unpack('<H',data[92:94])[0]
    header['header_size'] = struct.unpack('H',data[94:96])[0]
    header['point_data_offset'] = struct.unpack('<L',data[96:100])[0]
    header['num_variable_records'] = struct.unpack('<L',data[100:104])[0]
    header['point_data_format_id'] = struct.unpack('<B',data[104:105])[0]
    laz_format = False
    if header['point_data_format_id'] >= 128 and header['point_data_format_id'] <= 133:
        laz_format = True
        header['point_data_format_id'] = point_data_format_id - 128
    if laz_format:
        raise ValueError('LAZ not yet supported.')
    format_length = point_data_format_key[header['point_data_format_id']]
    header['point_data_record_length'] = struct.unpack('<H',data[105:107])[0]
    header['num_point_records'] = struct.unpack('<L',data[107:111])[0]
    header['num_points_by_return'] = struct.unpack('<5L',data[111:131])
    header['scale'] = struct.unpack('<3d',data[131:155])
    header['offset'] = struct.unpack('<3d',data[155:179])
    header['minmax'] = struct.unpack('<6d',data[179:227]) #xmax,xmin,ymax,ymin,zmax,zmin
    end_point_data = len(data)
    
    # For version 1.3, read in the location of the point data.  At this time
    # no wave information will be read
    header_length = 227
    if header['version']==1.3:
        header['begin_wave_form'] = struct.unpack('<q',data[227:235])[0]
        header_length = 235
        if header['begin_wave_form'] != 0:
            end_point_data = header['begin_wave_form']

    # Pare out only the point data
    data = data[header['point_data_offset']:end_point_data]

    if header['point_data_format_id']==1:
        dt = np.dtype([('x', 'i4'), ('y', 'i4'), ('z', 'i4'), ('intensity', 'u2'),
                       ('return_byte','u1'),('class','u1'),('scan_angle','u1'),
                       ('user_data','u1'),('point_source_id','u2'),('gpstime','f8')])
    
    elif header['point_data_format_id']==2:
        dt = np.dtype([('x', 'i4'), ('y', 'i4'), ('z', 'i4'), ('intensity', 'u2'),
                       ('return_byte','u1'),('class','u1'),('scan_angle','u1'),
                       ('user_data','u1'),('point_source_id','u2'),('red','u2'),
                       ('green','u2'),('blue','u2')])
    elif header['point_data_format_id']==3:
        dt = np.dtype([('x', 'i4'), ('y', 'i4'), ('z', 'i4'), ('intensity', 'u2'),
                       ('return_byte','u1'),('class','u1'),('scan_angle','u1'),
                       ('user_data','u1'),('point_source_id','u2'),('gpstime','f8'),
                       ('red','u2'),('green','u2'),('blue','u2')])
    elif header['point_data_format_id']==4:
        dt = np.dtype([('x', 'i4'), ('y', 'i4'), ('z', 'i4'), ('intensity', 'u2'),
                       ('return_byte','u1'),('class','u1'),('scan_angle','u1'),
                       ('user_data','u1'),('point_source_id','u2'),('gpstime','f8'),
                       ('wave_packet_descriptor_index','u1'),('byte_offset','u8'),
                       ('wave_packet_size','u4'),('return_point_waveform_location','f4'),
                       ('xt','f4'),('yt','f4'),('zt','f4')])
    elif header['point_data_format_id']==5:
        dt = np.dtype([('x', 'i4'), ('y', 'i4'), ('z', 'i4'), ('intensity', 'u2'),
                       ('return_byte','u1'),('class','u1'),('scan_angle','u1'),
                       ('user_data','u1'),('point_source_id','u2'),('gpstime','f8'),
                       ('red','u2'),('green','u2'),('blue','u2'),
                       ('wave_packet_descriptor_index','u1'),('byte_offset','u8'),
                       ('wave_packet_size','u4'),('return_point_waveform_location','f4'),
                       ('xt','f4'),('yt','f4'),('zt','f4')])

    # Transform to Pandas dataframe, via a numpy array
    data = pd.DataFrame(np.frombuffer(data,dt))
    data['x'] = data['x']*header['scale'][0] + header['offset'][0]
    data['y'] = data['y']*header['scale'][1] + header['offset'][1]
    data['z'] = data['z']*header['scale'][2] + header['offset'][2]

    def get_bit(byteval,idx):
        return ((byteval&(1<<idx))!=0);

    # Recast the return_byte to get return number (3 bits), the maximum return (3
    # bits), and the scan direction and edge of flight line flags (1 bit each)
    data['return_number'] = 4 * get_bit(data['return_byte'],2).astype(np.uint8) + 2 * get_bit(data['return_byte'],1).astype(np.uint8) + get_bit(data['return_byte'],0).astype(np.uint8)
    data['return_max'] = 4 * get_bit(data['return_byte'],5).astype(np.uint8) + 2 * get_bit(data['return_byte'],4).astype(np.uint8) + get_bit(data['return_byte'],3).astype(np.uint8)
    data['scan_direction'] = get_bit(data['return_byte'],6)
    data['edge_of_flight_line'] = get_bit(data['return_byte'],7)
    del data['return_byte']
    
    return header,data

#%%

# Using scipy's binned statistic would be preferable here, but it doesn't do
# min/max natively, and is too slow when not cython.
# Z,xi,yi,binnum = stats.binned_statistic_2d(x,y,z,statistic='min',bins=(x_edge,y_edge))
def create_dem(x,y,z,resolution=1,bin_type='max',use_binned_statistic=False):
    
    #x = df.x.values
    #y = df.y.values
    #z = df.z.values
    #resolution = 1
    #bin_type = 'max' 
    floor2 = lambda x,v: v*np.floor(x/v)
    ceil2 = lambda x,v: v*np.ceil(x/v)
    
    
    xedges = np.arange(floor2(np.min(x),resolution)-.5*resolution,
                       ceil2(np.max(x),resolution) + 1.5*resolution,resolution)
    yedges = np.arange(ceil2(np.max(y),resolution)+.5*resolution,
                       floor2(np.min(y),resolution) - 1.5*resolution,-resolution)
    nx, ny = len(xedges)-1,len(yedges)-1
    
    I = np.empty(nx*ny)
    I[:] = np.nan
    
    # Define an affine matrix, and convert realspace coordinates to integer pixel
    # coordinates
    t = rasterio.transform.from_origin(xedges[0], yedges[0], resolution, resolution)
    c,r = ~t * (x,y)
    c,r = np.floor(c).astype(np.int64), np.floor(r).astype(np.int64)
    
    # Old way:
    # Use pixel coordiantes to create a flat index; use that index to aggegrate, 
    # using pandas.
    if use_binned_statistic:
        I = stats.binned_statistic_2d(x,y,z,statistic='min',bins=(xedges,yedges))
    else:        
        mx = pd.DataFrame({'i':np.ravel_multi_index((r,c),(ny,nx)),'z':z}).groupby('i')
        del c,r
        if bin_type=='max':
            mx = mx.max()
        elif bin_type=='min':
            mx = mx.min()
        else:
            raise ValueError('This type not supported.')
        
        I.flat[mx.index.values] = mx.values
        I = I.reshape((ny,nx))
    
    return I,t


#%% Inpainting.  See research/current/inpaint/inpaint_nans.py for full details
# Finite difference approximation
def inpaint_nans_by_fda(A,fast=True,inplace=False):
    m,n = np.shape(A)
    nanmat = np.isnan(A)

    nan_list = np.flatnonzero(nanmat)
    known_list = np.flatnonzero(~nanmat)
    
    index = np.arange(m*n,dtype=np.int64).reshape((m,n))
    
    i = np.hstack( (np.tile(index[1:-1,:].ravel(),3),
                    np.tile(index[:,1:-1].ravel(),3)
                    ))
    j = np.hstack( (index[0:-2,:].ravel(),
                    index[2:,:].ravel(),
                    index[1:-1,:].ravel(),
                    index[:,0:-2].ravel(),
                    index[:,2:].ravel(),
                    index[:,1:-1].ravel()
                    ))
    data = np.hstack( (np.ones(2*n*(m-2),dtype=np.int64),
                       -2*np.ones(n*(m-2),dtype=np.int64),
                       np.ones(2*m*(n-2),dtype=np.int64),
                       -2*np.ones(m*(n-2),dtype=np.int64)
                       ))
    if fast==True:
        goodrows = np.in1d(i,index[ndi.binary_dilation(nanmat)])
        i = i[goodrows]
        j = j[goodrows]
        data = data[goodrows]
        del goodrows

    fda = sparse.coo_matrix((data,(i,j)),(m*n,m*n),dtype=np.int8).tocsr()
    del i,j,data,index
    
    rhs = -fda[:,known_list] * A[np.unravel_index(known_list,(m,n))]
    k = fda[:,np.unique(nan_list)]
    k = k.nonzero()[0]
    a = fda[k][:,nan_list]
    results = sparse.linalg.lsqr(a,rhs[k])[0]

    if inplace:
        A[np.unravel_index(nan_list,(m,n))] = results
    else:
        B = A.copy()
        B[np.unravel_index(nan_list,(m,n))] = results
        return B
        
    
#%%    
    
def unique_rows(a):
    a = np.ascontiguousarray(a)
    unique_a = np.unique(a.view([('', a.dtype)]*a.shape[1]))
    return unique_a.view(a.dtype).reshape((unique_a.shape[0], a.shape[1]))
            
# At the moment, only 4 neighbors are supported.
def inpaint_nans_by_springs(A,inplace=False,neighbors=4):

    m,n = np.shape(A)
    nanmat = np.isnan(A)

    nan_list = np.flatnonzero(nanmat)
    known_list = np.flatnonzero(~nanmat)
    
    r,c = np.unravel_index(nan_list,(m,n))
    
    num_neighbors = neighbors
    neighbors = np.array([[0,1],[0,-1],[-1,0],[1,0]]) #r,l,u,d

    neighbors = np.vstack([np.vstack((r+i[0], c+i[1])).T for i in neighbors])
    del r,c
    
    springs = np.tile(nan_list,num_neighbors)
    good_rows = (np.all(neighbors>=0,1)) & (neighbors[:,0]<m) & (neighbors[:,1]<n)
    
    neighbors = np.ravel_multi_index((neighbors[good_rows,0],neighbors[good_rows,1]),(m,n))
    springs = springs[good_rows]
    
    springs = np.vstack((springs,neighbors)).T
    del neighbors,good_rows
    
    springs = np.sort(springs,axis=1)
    springs = unique_rows(springs)
    
    n_springs = np.shape(springs)[0]
    
    i = np.tile(np.arange(n_springs),2)
    springs = springs.T.ravel()
    data = np.hstack((np.ones(n_springs,dtype=np.int8),-1*np.ones(n_springs,dtype=np.int8)))
    springs = sparse.coo_matrix((data,(i,springs)),(n_springs,m*n),dtype=np.int8).tocsr()
    del i,data
    
    rhs = -springs[:,known_list] * A[np.unravel_index(known_list,(m,n))]
    results = sparse.linalg.lsqr(springs[:,nan_list],rhs)[0]       

    if inplace:
        A[np.unravel_index(nan_list,(m,n))] = results
    else:
        B = A.copy()
        B[np.unravel_index(nan_list,(m,n))] = results
        return B
    
    
    
#%%
        
# TODO: add SMRF, add GEOMORPHONS, SWISS SHADING, more INTERPOlATORS


#%%
    
# ashift pulls a copy of the raster shifted.  0 shifts upper-left to lower right
# 1 shifts top-to-bottom, etc.  Clockwise from upper left.
def ashift(surface,direction,n=1):
    surface = surface.copy()
    if direction==0:
        surface[n:,n:] = surface[0:-n,0:-n]
    elif direction==1:
        surface[n:,:] = surface[0:-n,:]
    elif direction==2:
        surface[n:,0:-n] = surface[0:-n,n:]
    elif direction==3:
        surface[:,0:-n] = surface[:,n:]
    elif direction==4:
        surface[0:-n,0:-n] = surface[n:,n:]
    elif direction==5:
        surface[0:-n,:] = surface[n:,:]
    elif direction==6:
        surface[0:-n,n:] = surface[n:,0:-n]
    elif direction==7:
        surface[:,n:] = surface[:,0:-n]
    return surface


#%%

def openness(Z,cellsize=1,lookup_pixels=1,neighbors=np.arange(8)):

    nrows, ncols = np.shape(Z)
        
    # neighbor directions are clockwise from top left,starting at zero
    # neighbors = np.arange(8)   
    
    # Define a (fairly large) 3D matrix to hold the minimum angle for each pixel
    # for each of the requested directions (usually 8)
    opn = np.Inf * np.ones((len(neighbors),nrows,ncols))
    
    # Define an array to calculate distances to neighboring pixels
    dlist = np.array([np.sqrt(2),1])

    # Calculate minimum angles        
    for L in np.arange(lookup_pixels)+1:
        for i,direction in enumerate(neighbors):
            # Map distance to this pixel:
            dist = dlist[direction % 2]
            dist = cellsize * L * dist
            # Angle is the arctan of the difference in elevations, divided by distance
            these_angles = (np.pi/2) - np.arctan((ashift(Z,direction,L)-Z)/dist)
            this_layer = opn[i,:,:]
            this_layer[these_angles < this_layer] = these_angles[these_angles < this_layer]
            opn[i,:,:] = this_layer

    # Openness is definted as the mean of the minimum angles of all 8 neighbors        
    return np.rad2deg(np.mean(opn,0))




#%%
    
# This routine uses openness to generate a ternary pattern based on the 
# difference of the positive and negative openness values.  If the difference
# is above a supplied threshold, the value is "high" or 2.  If the difference
# is below the threshold, it is 1 or "equal".  If the difference is less than 
# the negative threshold, it is 0 or "low".
    
# The algorithm proceeds through each 8 directions, one at a time, building
# a list of 8 ternary values (e.g., 21120210).  Previously, these would have 
# been recorded, and then converted to decimal; here they are converted
# to decimal as it progresses.  Upper left pixel is the least significant
# digit, left pixel is the most significant pixel.
    
def ternary_pattern_from_openness(Z,cellsize=1,lookup_pixels=1,threshold_angle=0):
    pows = 3**np.arange(8)
    #bc = np.zeros(np.shape(Z),dtype=np.uint32)
    tc = np.zeros(np.shape(Z),dtype=np.uint16)
    f = 1
    for i in range(8):
        O = openness(Z,cellsize,lookup_pixels,neighbors=np.array([i]))
        O = O - openness(-Z,cellsize,lookup_pixels,neighbors=np.array([i]))
        tempMat = np.ones(np.shape(tc),dtype=np.uint32)
        tempMat[O > threshold_angle] = 2;
        tempMat[O < -threshold_angle] = 0;
    
        # Record the result.
        #bc = bc + f*tempMat;
        tc = tc + tempMat*pows[i] 
    
        # Increment f
        f = f * 10;
    
    return tc


#%%
# Adapted from https://stackoverflow.com/questions/2267362/how-to-convert-an-integer-in-any-base-to-a-string
def int2base(x,b,alphabet='0123456789abcdefghijklmnopqrstuvwxyz',min_digits=8):
    rets=''
    while x>0:
        x,idx = divmod(x,b)
        rets = alphabet[idx] + rets
    if len(rets) < min_digits:
        pad = ''
        for i in range(min_digits - len(rets)):
            pad = pad + '0'
        rets = pad + rets
    return rets



#%%
    
def get_all_equivalents(values = np.arange(3**8)):
    def get_equivalent(i):
        s = int2base(i,3)
        min_val = int(s,3)
        for j in range(1,16):
            s = s[-1] + s[:7]
            min_val = min(min_val,int(s,3))
            if j==7:
                s = s[::-1]
        return min_val
    
    for i in values:
        values[i] = get_equivalent(i)
    values = np.array(values)
    return values


#%%
def get_geomorphon(terrain_code,method='strict'):
    method_options = ['strict','loose']
    if method not in method_options:    
        geomorphon = None
        lookup_table = np.zeros(3**8,np.uint8)
        if method=='strict':
            lookup_table[3280] = 1  # Flat
            lookup_table[0] = 2     # Peak
            lookup_table[82] = 3    # Ridge
            lookup_table[121] = 4   # Shoulder
            lookup_table[26] = 5    # Spur
            lookup_table[160] = 6   # Slope
            lookup_table[242] = 7   # Hollow
            lookup_table[3293] = 8  # Footslope
            lookup_table[4346] = 9  # Valley
            lookup_table[6560] = 10 # Pit
        elif method=='loose':
            lookup_table = np.zeros(3**8,np.uint8)
            strict_table = np.zeros((9,9),dtype=np.uint8)
            strict_table[0,:]   = [1,1,1,8,8,9,9,9,10]
            strict_table[1,:8]  = [1,1,8,8,8,9,9,9]
            strict_table[1,:7]  = [1,4,6,6,7,7,9]
            strict_table[1,:6]  = [4,4,6,6,6,7]
            strict_table[1,:5]  = [4,4,5,6,6]
            strict_table[1,:4]  = [3,3,5,5]
            strict_table[1,:3]  = [3,3,3]
            strict_table[1,:2]  = [3,3]
            strict_table[1,:1]  = [3]
            for i in range(3**8):
                base = int2base(i,3)
                r,c = base.count('0'), base.count('2')
                lookup_table[i] = strict_table[r,c]
            geomorphon = lookup_table[terrain_code].astype(np.uint8)
        return geomorphon
        
#%%
lookup_table = np.zeros(3**8,np.uint8)
strict_table = np.zeros((9,9),dtype=np.uint8)
strict_table[0,:]   = [1,1,1,8,8,9,9,9,10]
strict_table[1,:8]  = [1,1,8,8,8,9,9,9]
strict_table[1,:7]  = [1,4,6,6,7,7,9]
strict_table[1,:6]  = [4,4,6,6,6,7]
strict_table[1,:5]  = [4,4,5,6,6]
strict_table[1,:4]  = [3,3,5,5]
strict_table[1,:3]  = [3,3,3]
strict_table[1,:2]  = [3,3]
strict_table[1,:1]  = [3]
for i in range(3**8):
    base = int2base(i,3)
    r,c = base.count('0'), base.count('2')
    lookup_table[i] = strict_table[r,c]
    


#%%
#
terrain_code = ternary_pattern_from_openness(Z,cellsize=Zt[0],lookup_pixels=20,threshold_angle=1)
lookup_table= get_all_equivalents()
# https://stackoverflow.com/questions/14448763/is-there-a-convenient-way-to-apply-a-lookup-table-to-a-large-array-in-numpy

terrain_code = lookup_table[terrain_code]
geomorphon = get_geomorphon(terrain_code)
#%%
#S = slope(Z,src.transform[0])   
#A = aspect(Z)    
#H = hillshade(Z,cellsize=src.transform[0],z_factor=1)
#plt.imshow(H,cmap='gray',vmin=0,vmax=255,aspect='equal')

#%%
#H = multiple_illumination(Z,cellsize=src.transform[0],z_factor=1,zeniths=2,azimuths=3);
#plt.imshow(H,cmap='gray',aspect='equal')

#%%
#P = pssm(Z,cellsize=src.transform[0],reverse=True)
#plt.imshow(P,aspect='equal')

#%%


