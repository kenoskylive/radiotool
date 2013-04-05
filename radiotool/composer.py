# composer.py
# (c) 2012 - Steve Rubin - srubin@cs.berkeley.edu
# Has all the classes for speech, songs, and fade types
# Additionally, has class for actual composition

import sys

from math import sqrt

import numpy as N

from scikits.audiolab import Sndfile, Format

# import scikits.talkbox as talk
import segmentaxis
import mfcc
from scipy.spatial import distance

# problem on the server with resample...
#from scipy.signal import resample

# import arraypad
from numpy import pad as arraypad
### Uncomment for MATLAB
# from mlabwrap import mlab as matlab

### note - part of mfcc:
# m = mfcc.MFCC(samprate=s.sr(), wlen=0.1)
# b = m.frame2logspec(f.reshape(2,-1)[1]) clip??

LOG_TO_DB = False
DEBUG = False

if LOG_TO_DB:
    import MySQLdb


def log_magnitude_spectrum(window):
    return N.log(N.abs(N.fft.rfft(window)).clip(1e-5, N.inf))


def magnitude_spectrum(window):
    return N.abs(N.fft.rfft(window))


def RMS_energy(frames):
    f = frames.flatten()
    return N.sqrt(N.mean(f * f))


def IS_distance(p1, p2):
    """Calculate the Itakura-Saito spectral distance between power spectra"""
    # see
    # http://www.ee.ic.ac.uk/hp/staff/dmb/voicebox/doc/voicebox/distispf.html
    # but implementing this...
    # http://hil.t.u-tokyo.ac.jp/~kameoka/SAP/papers/
    #  El-Jaroudi1991__Discrete-All_Pole_Modeling.pdf
    # equation 14

    if len(N.where(p2 == 0)[0]) > 0:
        return 0
    q = p1 / p2
    if N.isinf(N.mean(q - N.log(q))):
        return 0
    return N.mean(q - N.log(q)) - 1


def COSH_distance(p1, p2):
    """IS distance is asymmetric, so this accounts for that"""
    return (IS_distance(p1, p2) + IS_distance(p2, p1)) / 2


def robust_logistic_regression(features):
    mu = N.mean(features)
    sigma = N.std(features)
    gamma = N.log(99) # natural log
    return 1 / (1 + N.exp(-gamma * (features - mu) / sigma))


def normalize_features(features):
    return (features - N.min(features)) / (N.max(features) - N.min(features))


def zero_crossing_last(frames):
    """finds the first zero crossing in frames before frame n"""
    frames = N.array(frames)

    crossings = N.where(N.diff(N.sign(frames)))
    # crossings = N.where(frames[:n] * frames[1:n + 1] < 0)

    if len(crossings[0]) == 0:
        print "No zero crossing"
        return len(frames) - 1
    return crossings[0][-1]


def zero_crossing_first(frames):
    """finds the first zero crossing in frames after frame n"""
    frames = N.array(frames)
    crossings = N.where(N.diff(N.sign(frames)))
    # crossings = N.where(frames[n - 1:-1] * frames[n:] < 0)
    if len(crossings[0]) == 0:
        print "No zero crossing"
        return 0
    return crossings[0][0] + 1

# Crossfading helper methods
# borrowed from echonest remix

def log_factor(arr):
    return N.power(arr, 0.6)


def limiter(arr):
    dyn_range = 32767.0 / 32767.0
    lim_thresh = 30000.0 / 32767.0
    lim_range = dyn_range - lim_thresh

    new_arr = arr.copy()
    
    inds = N.where(arr > lim_thresh)[0]

    new_arr[inds] = (new_arr[inds] - lim_thresh) / lim_range
    new_arr[inds] = (N.arctan(new_arr[inds]) * 2.0 / N.pi) *\
        lim_range + lim_thresh

    inds = N.where(arr < -lim_thresh)[0]

    new_arr[inds] = -(new_arr[inds] + lim_thresh) / lim_range
    new_arr[inds] = -(
        N.arctan(new_arr[inds]) * 2.0 / N.pi * lim_range + lim_thresh)

    return new_arr

def linear(arr1, arr2):
    n = N.shape(arr1)[0]
    try: 
        channels = N.shape(arr1)[1]
    except:
        channels = 1
    
    f_in = N.arange(n) / float(n - 1)
    f_out = N.arange(n - 1, -1, -1) / float(n)
    
    if channels > 1:
        f_in = N.tile(f_in, (channels, 1)).T
        f_out = N.tile(f_out, (channels, 1)).T
    
    vals = f_out * arr1 + f_in * arr2
    return vals

def equal_power(arr1, arr2):
    n = N.shape(arr1)[0]
    try: 
        channels = N.shape(arr1)[1]
    except:
        channels = 1
    
    f_in = N.arange(n) / float(n - 1)
    f_out = N.arange(n - 1, -1, -1) / float(n)
    
    if channels > 1:
        f_in = N.tile(f_in, (channels, 1)).T
        f_out = N.tile(f_out, (channels, 1)).T
    
    vals = log_factor(f_out) * arr1 + log_factor(f_in) * arr2

    return limiter(vals)


class Track:
    
    def __init__(self, fn, name="No name"):
        """Create a Track object from a music filename"""
        self.filename = fn
        self.name = name
        try:
            self.sound = Sndfile(self.filename, 'r')
            self.current_frame = 0
            self.channels = self.sound.channels
        except:
            print 'Could not open track: %s' % self.filename

    def read_frames(self, n):
        if self.channels == 1:
            out = N.zeros(n)
        elif self.channels == 2:
            out = N.zeros((n, 2))
        else:
            print "Input needs to have 1 or 2 channels"
            return
        if n > self.remaining_frames():
            print "Trying to retrieve too many frames!"
            print "Asked for", n
            n = self.remaining_frames()
        self.current_frame += n
        
        if self.channels == 1:
            out = self.sound.read_frames(n)
        elif self.channels == 2:
            out[:n, :] = self.sound.read_frames(n)
        return out

    def set_frame(self, n):
        self.sound.seek(n)
        self.current_frame = n

    def reset(self):
        self.set_frame(0)
        self.current_frame = 0

    def all_as_mono(self):
        """Get the entire track as 1 combined channel"""
        return self.range_as_mono(0, self.total_frames())

    def range_as_mono(self, start_sample, end_sample):
        """Get a range of frames as 1 combined channel"""
        tmp_current = self.current_frame
        self.set_frame(start_sample)
        tmp_frames = self.read_frames(end_sample - start_sample)
        if self.channels == 2:
            frames = N.mean(tmp_frames, axis=1)
        elif self.channels == 1:
            frames = tmp_frames
        else:
            raise IOError("Input audio must have either 1 or 2 channels")
        self.set_frame(tmp_current)
        return frames

    def samplerate(self):
        return self.sound.samplerate
        
    def sr(self):
        return self.samplerate()

    def remaining_frames(self):
        return self.sound.nframes - self.current_frame
        
    def total_frames(self):
        return self.sound.nframes
    
    def duration(self):
        return self.total_frames() / float(self.samplerate())
        
    def loudest_time(self, start=0, duration=0):
        """Find the loudest time in the window given by start and duration
        Returns frame number in context of entire track, not just the window
        """
        if duration == 0:
            duration = self.sound.nframes
        self.set_frame(start)
        arr = self.read_frames(duration)
        # get the frame of the maximum amplitude
        # different names for the same thing...
        # max_amp_sample = a.argmax(axis=0)[a.max(axis=0).argmax()]
        max_amp_sample = int(N.floor(arr.argmax()/2)) + start
        return max_amp_sample
    
    def refine_cut(self, cut_point, window_size=1):
        return cut_point
        
    def zero_crossing_before(self, n):
        """n is in seconds, finds the first zero crossing before n seconds"""
        n_in_samples = int(n * self.samplerate())

        search_start = n_in_samples - self.samplerate() 
        if search_start < 0:
            search_start = 0

        frame = zero_crossing_last(
            self.range_as_mono(search_start, n_in_samples)) + search_start

        # frame = zero_crossing_before(self.all_as_mono(), n_in_samples)
        return frame / float(self.samplerate())

    def zero_crossing_after(self, n):
        n_in_samples = int(n * self.samplerate())
        search_end = n_in_samples + self.samplerate()
        if search_end > self.total_frames():
            search_end = self.total_frames()

        frame = zero_crossing_first(
            self.range_as_mono(n_in_samples, search_end)) + n_in_samples

        # frame = zero_crossing_after(self.all_as_mono(), n_in_samples)
        return frame / float(self.samplerate())


class RawTrack(Track):

    def __init__(self, frames, name="Raw frames name", samplerate=44100):
        self._sr = samplerate
        self.frames = frames
        self.name = name
        self.filename = "RAW_" + name
        try:
            self.channels = N.shape(frames)[1]
        except:
            self.channels = 1
        self.current_frame = 0
        self._total_frames = N.shape(frames)[0]
    
    def samplerate(self):
        return self._sr
    
    def sr(self):
        return self._sr
    
    def set_frame(self, n):
        self.current_frame = n
    
    def total_frames(self):
        return self._total_frames
    
    def remaining_frames(self):
        return self._total_frames - self.current_frame
    
    def reset(self):
        self.current_frame = 0
    
    def read_frames(self, n):
        if self.channels == 1:
            out = N.zeros(n)
        elif self.channels == 2:
            out = N.zeros((n, 2))
        else:
            print "Input needs to have 1 or 2 channels"
            return
        if n > self.remaining_frames():
            print "Trying to retrieve too many frames!"
            print "Asked for", n
            n = self.remaining_frames()

        if self.channels == 1:
            out = self.frames[self.current_frame:self.current_frame + n]
        elif self.channels == 2:
            out[:n, :] = self.frames[
                self.current_frame:self.current_frame + n, :]

        self.current_frame += n
        return out


class Song(Track):

    def __init__(self, fn, name="Song name"):
        Track.__init__(self, fn, name)
        
    def magnitude_spectrum(self, window):
        """Compute the magnitude spectra"""
        return N.abs(N.fft.rfft(window))
        
    def partial_mfcc(self, window):
        """partial mfcc calculation (stopping before mel band filter)"""
  
        dump_out["names"] = ('MFCC euclidean distance',
                             'RMS energy distance',
                             'Chromagram COSH distance',
                             'Chromagram euclidean distance',
                             'Tempo difference',
                             'Magnitude spectra COSH distance',
                             'RMS energy')
   
    def refine_cut_by(self, refinement, cut_point, window_size=4):
        if refinement == "RMS energy distance":
            return self.refine_cut_rms_jump(cut_point, window_size)
        elif refinement == "MFCC euclidean distance":
            return self.refine_cut_mfcc_euc(cut_point, window_size)
        elif refinement == "Chromagram euclidean distance":
            return self.refine_cut_chroma_euc(cut_point, window_size)
            
        return self.refine_cut_rms_jump(cut_point, window_size)
    
    def refine_cut_rms_jump(self, cut_point, window_size=4):
        # subwindow length
        swlen = 0.250 # 250ms 
        
        start_frame = int((cut_point - window_size * 0.5) * self.sr())
        if (start_frame < 0):
            start_frame = 0
        
        if (start_frame + window_size * self.sr() > self.total_frames()):
            start_frame = self.total_frames() - window_size * self.sr() - 1
            
        self.set_frame(start_frame)
        tmp_frames = self.read_frames(window_size * self.sr())
        
        subwindow_n_frames = swlen * self.sr()
        
        # add left and right channels
        frames = N.empty(window_size * self.sr())
        for i in range(len(frames)):
            frames[i] = tmp_frames[i][0] + tmp_frames[i][1]
        segments = segmentaxis.segment_axis(frames, subwindow_n_frames, axis=0,
        overlap=int(subwindow_n_frames * 0.5))  
        
        RMS_energies = N.apply_along_axis(RMS_energy, 1, segments) 
           
        energy_diffs = N.zeros(len(RMS_energies))
        energy_diffs[1:] = RMS_energies[1:] - RMS_energies[:-1]
        idx = N.where(energy_diffs == max(energy_diffs))[0][0]
        return round(cut_point - window_size * 0.5 +
                           idx * swlen * 0.5, 2), \
               normalize_features(energy_diffs)

    def refine_cut_mfcc_euc(self, cut_point, window_size=4):
        return self.refine_cut_mfcc(cut_point, window_size, "euclidean")
    
    def refine_cut_mfcc(self, cut_point, window_size=4, dist="euclidean"):
        # subwindow length
        swlen = 0.250 #  
        
        self.set_frame(int((cut_point - window_size * 0.5) * self.sr()))
        tmp_frames = self.read_frames(window_size * self.sr())
        
        subwindow_n_frames = swlen * self.sr()
        
        # add left and right channels
        frames = N.empty(window_size * self.sr())
        for i in range(len(frames)):
            frames[i] = tmp_frames[i][0] + tmp_frames[i][1]
        
        segments = segmentaxis.segment_axis(frames, subwindow_n_frames, axis=0,
        overlap=int(subwindow_n_frames * 0.5))
        # compute MFCCs, compare Euclidean distance
        m = mfcc.MFCC(samprate=self.sr(), wlen=swlen)
        mfccs = N.apply_along_axis(m.frame2s2mfc, 1, segments)
        mfcc_dists = N.zeros(len(mfccs))
        for i in range(1,len(mfcc_dists)):
            if dist == "euclidean":
                mfcc_dists[i] = N.linalg.norm(mfccs[i-1] - mfccs[i])
            elif dist == "cosine":
                mfcc_dists[i] = distance.cosine(mfccs[i-1], mfccs[i])
        if DEBUG: print "MFCC euclidean distances: ", mfcc_dists
        idx = N.where(mfcc_dists == max(mfcc_dists))[0][0]
        return round(cut_point - window_size * 0.5 +
                           idx * swlen * 0.5, 2), \
               normalize_features(mfcc_dists)
                           
    def refine_cut_chroma_euc(self, cut_point, window_size=4):
        # subwindow length
        swlen = 0.24 #  
        
        self.set_frame(int((cut_point - window_size * 0.5) * self.sr()))
        tmp_frames = self.read_frames(window_size * self.sr())
        
        subwindow_n_frames = swlen * self.sr()
        
        # add left and right channels
        frames = N.empty(window_size * self.sr())
        for i in range(len(frames)):
            frames[i] = tmp_frames[i][0] + tmp_frames[i][1]
        
        segments = segmentaxis.segment_axis(frames, subwindow_n_frames, axis=0,
        overlap=int(subwindow_n_frames * 0.5))
        # compute chromagram
        fftlength = 44100 * swlen
        # this compute with 3/4 overlapping windows and we want
        # 1/2 overlapping, so we'll take every other column
        cgram = matlab.chromagram_IF(frames, 44100, fftlength)
        # don't need to get rid of 3/4 overlap because we're using it
        # on its own
        # cgram_idx = range(0, len(cgram[0,:]), 2)
        # cgram = cgram[:,cgram_idx]
        cgram_euclidean = N.array([N.linalg.norm(cgram[:,i] - cgram[:,i+1])
                                  for i in range(len(cgram[0,:])-1)])
        idx = N.where(cgram_euclidean == max(cgram_euclidean))[0][0]
        return round(cut_point - window_size * 0.5 +
                     (idx + 1) * swlen * .25, 2), ()
        
    def refine_cut(self, cut_point, window_size=2, scored=True):
        # these should probably all be computed elsewhere and merged
        # (scored?) here
        
        cut_idx = {}
        features = {}
        
        # subwindow length
        swlen = 0.1 # 100ms 
        
        self.set_frame(int((cut_point - window_size * 0.5) * self.sr()))
        tmp_frames = self.read_frames(window_size * self.sr())
        
        subwindow_n_frames = swlen * self.sr()
        
        # add left and right channels
        frames = N.empty(window_size * self.sr())
        for i in range(len(frames)):
            frames[i] = tmp_frames[i][0] + tmp_frames[i][1]
        
        segments = segmentaxis.segment_axis(frames, subwindow_n_frames, axis=0,
                                     overlap=int(subwindow_n_frames * 0.5))

        # should I not use the combined left+right for this feature?
        RMS_energies = N.apply_along_axis(RMS_energy, 1, segments)
        
        if DEBUG: print "RMS energies: ", RMS_energies
        # this is probably not a great feature
        #features["rms_energy"] = RMS_energies
        cut_idx["rms_energy"] = N.where(RMS_energies == max(RMS_energies))[0][0]
        
        ## do it by biggest jump between windows instead
        ## disregard overlapping windows for now
        energy_diffs = N.zeros(len(RMS_energies))
        energy_diffs[1:] = RMS_energies[1:] - RMS_energies[:-1]
        if DEBUG: print "energy differences: ", energy_diffs
        features["rms_jump"] = energy_diffs
        cut_idx["rms_jump"] = N.where(energy_diffs == max(energy_diffs))[0][0]
        
        # compute power spectra, compare differences with I-S distance
        magnitude_spectra = N.apply_along_axis(self.magnitude_spectrum,
                                               1, segments)
        #IS_ms_distances = N.zeros(len(magnitude_spectra))
        # is there a better way... list comprehensions with numpy?
        # for i in range(1,len(IS_ms_distances)):
        #     # not symmetric... do average?
        #     IS_ms_distances[i] = COSH_distance(magnitude_spectra[i-1],
        #                                   magnitude_spectra[i])
        IS_ms_distances = N.array([
            COSH_distance(magnitude_spectra[i],
                          magnitude_spectra[i+1])
            for i in range(len(magnitude_spectra)-1)])
        IS_ms_distances = N.append(IS_ms_distances, 0)
        
        if DEBUG: print "IS ms distances", IS_ms_distances
        features["magnitude_spectra_COSH"] = IS_ms_distances
        cut_idx["magnitude_spectra_COSH"] = N.where(
                IS_ms_distances == max(IS_ms_distances))[0][0] + 1
                
        # compute MFCCs, compare Euclidean distance
        m = mfcc.MFCC(samprate=self.sr(), wlen=swlen)
        mfccs = N.apply_along_axis(m.frame2s2mfc, 1, segments)
        mfcc_dists = N.zeros(len(mfccs))
        for i in range(1,len(mfcc_dists)):
            mfcc_dists[i] = N.linalg.norm(mfccs[i-1] - mfccs[i])
        if DEBUG: print "MFCC euclidean distances: ", mfcc_dists
        features["mfcc_euclidean"] = mfcc_dists
        cut_idx["mfcc_euclidean"] = N.where(mfcc_dists ==
                                            max(mfcc_dists))[0][0]
        
         
        
        combined_features = N.zeros(len(segments))
        for k, v in features.iteritems():
            combined_features += (v - min(v)) / (max(v)- min(v))
        
        cut_idx["combined"] = N.where(combined_features == 
                                      max(combined_features))[0][0]
        if DEBUG: print 'Combined features: ', combined_features
        
        IDX = 'mfcc_euclidean'
        if DEBUG: print "Using ", IDX
                                  
        for k, v in cut_idx.iteritems():
            cut_idx[k] = round(cut_point - window_size * 0.5 +
                               v * swlen * 0.5, 2)
            
        from pprint import pprint            
        if DEBUG: pprint(cut_idx)
        
        # log results to DB for later comparison
        if LOG_TO_DB:
            try:
                con = MySQLdb.connect('localhost', 'root',
                                      'qual-cipe-whak', 'music')
                cur = con.cursor(MySQLdb.cursors.DictCursor)
                desc = "Highest MFCC euclidean distance " + \
                       "with 4 second window, .1 second subwindow and " + \
                       "euclidean distance MFCC segmentation (4 second window)"
                method_q = "SELECT * FROM methods WHERE description = '%s'" \
                            % desc
                cur.execute(method_q)
                method = cur.fetchone()
            
                if method is None:
                    query = "INSERT INTO methods(description) VALUES('%s')" \
                            % desc
                    cur.execute(query)
                    cur.execute(method_q)
                    method = cur.fetchone()
                
                method_id = method["id"]
            
                fn = '.'.join(self.filename.split('/')[-1]
                              .split('.')[:-1]) + '%'
                song_q = "SELECT * FROM songs WHERE filename LIKE %s"
                cur.execute(song_q, fn)
                song = cur.fetchone()
            
                if song is None:
                    print "Could not find song in db matching filename %s" % (
                        filename)
                    return cut_idx[IDX]

                song_id = song["id"]

                result_q = "INSERT INTO results(song_id, song_cutpoint, " + \
                           "method_id) VALUES(%d, %f, %d)" % (song_id, 
                           cut_idx[IDX], method_id)
                cur.execute(result_q)
            
            except MySQLdb.Error, e:
                print "Error %d: %s" % (e.args[0], e.args[1])

            finally:
                if cur:
                    cur.close()
                if con:
                    con.commit()
                    con.close()
        
        return cut_idx[IDX]
    
class Speech(Track):
    def __init__(self, fn, name="Speech name"):
        Track.__init__(self, fn, name)
    
    def refine_cut(self, cut_point, window_size=1):
        self.set_frame(int((cut_point - window_size / 2.0) * self.sr()))
        frames = self.read_frames(window_size * self.sr())
        subwindow_n_frames = int((window_size / 16.0) * self.sr())

        segments = segmentaxis.segment_axis(frames, subwindow_n_frames, axis=0,
                                     overlap=int(subwindow_n_frames / 2.0))

        segments = segments.reshape((-1, subwindow_n_frames * 2))
        #volumes = N.mean(N.abs(segments), 1)
        volumes = N.apply_along_axis(RMS_energy, 1, segments)
 
        if DEBUG: print volumes
        min_subwindow_vol = min(N.sum(N.abs(segments), 1) / subwindow_n_frames)
        min_subwindow_vol = min(volumes)
        if DEBUG: print min_subwindow_vol
        # some threshold? what if there are no zeros?
        
        min_subwindow_vol_index = N.where(volumes <= 1.1 * 
                                          min_subwindow_vol)

        # first_min_subwindow = min_subwindow_vol_index[0][0]
        # closest_min_subwindow = find_nearest(min_subwindow_vol_index[0], 
        #                                      len(volumes)/2)
        
        # find longest span of "silence" and set to the beginning
        # adapted from 
        # http://stackoverflow.com/questions/3109052/
        # find-longest-span-of-consecutive-array-keys
        last_key = -1
        cur_list = []
        long_list = []
        for idx in min_subwindow_vol_index[0]:
            if idx != last_key + 1:
                cur_list = []
            cur_list.append(idx)
            if(len(cur_list) > len(long_list)):
                long_list = cur_list
            last_key = idx
        
        new_cut_point = (self.sr() * (cut_point - window_size / 2.0) + 
                         (long_list[0] + 1) * 
                         int(subwindow_n_frames / 2.0))
        print "first min subwindow", long_list[0], "total", len(volumes)
        return round(new_cut_point / self.sr(), 2)
        # have to add the .5 elsewhere to get that effect!
        
class Segment:
    # score_location, start, and duration all in seconds
    # -- may have to change later if this isn't accurate enough
    def __init__(self, track, score_location, start, duration):
        self.samplerate = track.samplerate()
        self.track = track
        self.score_location = int(score_location * self.samplerate)
        self.start = int(start * self.samplerate)
        self.duration = int(duration * self.samplerate)

    def get_frames(self, channels=2):
        self.track.set_frame(self.start)
        frames = self.track.read_frames(self.duration)
        self.track.set_frame(0)
        
        if channels == self.track.channels:
            return frames.copy()
        elif channels == 2 and self.track.channels == 1:
            return N.hstack((frames.copy(), frames.copy()))
        elif channels == 1 and self.track.channels == 2:
            return N.mean(frames, axis=1)

class TimeStretchSegment(Segment):
    #from scipy.signal import resample

    def __init__(self, track, score_location, start, orig_duration, new_duration):
        Segment.__init__(self, track, score_location, start, new_duration)
        self.orig_duration = int(orig_duration * self.samplerate)

    def get_frames(self, channels=2):
        self.track.set_frame(self.start)
        frames = self.track.read_frames(self.orig_duration)
        frames = resample(frames, self.duration)
        self.track.set_frame(0)
        return frames
        
class Dynamic:
    def __init__(self, track, score_location, duration):
        self.track = track
        self.samplerate = track.samplerate()
        self.score_location = int(round(score_location * self.samplerate))
        self.duration = int(round(duration * self.samplerate))
        
    def to_array(self, channels=2):
        return N.ones( (self.duration, channels) )
        
    def __str__(self):
        return "Dynamic at %d with duration %d" % (self.score_location,
                                                   self.duration)
        
class Volume(Dynamic):
    def __init__(self, track, score_location, duration, volume):
        Dynamic.__init__(self, track, score_location, duration)
        self.volume = volume
        
    def to_array(self, channels=2):
        return N.linspace(self.volume, self.volume, 
            self.duration * channels).reshape(self.duration, channels)

class RawVolume(Dynamic):
    def __init__(self, segment, volume_frames):
        self.track = segment.track
        self.samplerate = segment.track.samplerate()
        self.score_location = segment.score_location
        self.duration = segment.duration
        self.volume_frames = volume_frames
        if self.duration != len(volume_frames):
            raise Exception("Duration must be same as volume frame length")
    
    def to_array(self, channels=2):
        if channels == 1:
            return self.volume_frames.reshape(-1, 1)
        if channels == 2:
            return N.tile(self.volume_frames, (1, 2))
        raise Exception(
            "RawVolume doesn't know what to do with %s channels" % channels)
        

class Fade(Dynamic):
    # linear, exponential, (TODO: cosine)
    def __init__(self, track, score_location, duration, 
                in_volume, out_volume, fade_type="linear"):
        Dynamic.__init__(self, track, score_location, duration)
        self.in_volume = in_volume
        self.out_volume = out_volume
        self.fade_type = fade_type
        
    def to_array(self, channels=2):
        if self.fade_type == "linear":
            return N.linspace(self.in_volume, self.out_volume, 
                self.duration * channels)\
                .reshape(self.duration, channels)
        elif self.fade_type == "exponential":
            if self.in_volume < self.out_volume:
                return (N.logspace(8, 1, self.duration * channels,
                    base=.5) * (
                        self.out_volume - self.in_volume) / 0.5 + 
                        self.in_volume).reshape(self.duration, channels)
            else:
                return (N.logspace(1, 8, self.duration * channels, base=.5
                    ) * (self.in_volume - self.out_volume) / 0.5 + 
                    self.out_volume).reshape(self.duration, channels)
        elif self.fade_type == "cosine":
            return

class Composition:
    def __init__(self, tracks=[], channels=2):
        self.tracks = set(tracks)
        self.score = []
        self.dynamics = []
        self.channels = channels

    def add_track(self, track):
        self.tracks.add(track)
    
    def add_tracks(self, tracks):
        self.tracks.update(tracks)
        
    def add_score_segment(self, segment):
        self.score.append(segment)
        
    def add_score_segments(self, segments):
        self.score.extend(segments)

    def add_dynamic(self, dyn):
        self.dynamics.append(dyn)
        
    def add_dynamics(self, dyns):
        self.dynamics.extend(dyns)

    def fade_in(self, segment, duration):
        """adds a fade in (duration in seconds)"""
        dur = int(round(duration * segment.track.samplerate()))
        score_loc_in_seconds = (segment.score_location) /\
            float(segment.track.samplerate())
        f = Fade(segment.track, score_loc_in_seconds, duration, 0.0, 1.0)
        self.add_dynamic(f)
        return f

    def fade_out(self, segment, duration):
        """adds a fade out (duration in seconds)"""
        dur = int(round(duration * segment.track.samplerate()))
        score_loc_in_seconds = (segment.score_location + segment.duration - dur) /\
            float(segment.track.samplerate())
        f = Fade(segment.track, score_loc_in_seconds, duration, 1.0, 0.0)
        self.add_dynamic(f)
        return f

    def extended_fade_in(self, segment, duration):
        """extends the beginning of the segment and adds a fade in
        (duration in seconds)"""
        dur = int(round(duration * segment.track.samplerate()))
        if segment.start - dur >= 0:
            segment.start -= dur
        else:
            raise Exception(
                "Cannot create fade-in that extends past the track's beginning")
        if segment.score_location - dur >= 0:
            segment.score_location -= dur
        else:
            raise Exception(
                "Cannot create fade-in the extends past the score's beginning")

        segment.duration += dur
        
        score_loc_in_seconds = (segment.score_location) /\
            float(segment.track.samplerate())

        f = Fade(segment.track, score_loc_in_seconds, duration, 0.0, 1.0)
        self.add_dynamic(f)
        return f

    def extended_fade_out(self, segment, duration):
        """extends the end of the segment and adds a fade out
        (duration in seconds)"""
        dur = int(round(duration * segment.track.samplerate()))
        if segment.start + segment.duration + dur <\
            segment.track.total_frames():
            segment.duration += dur
        else:
            raise Exception(
                "Cannot create fade-out that extends past the track's end")
        score_loc_in_seconds = (segment.score_location + segment.duration - dur) /\
            float(segment.track.samplerate())
        f = Fade(segment.track, score_loc_in_seconds, duration, 1.0, 0.0)
        self.add_dynamic(f)
        return f
    
    def cross_fade(self, seg1, seg2, duration):
        """equal power crossfade"""
        if seg1.score_location + seg1.duration - seg2.score_location < 2:
            dur = int(duration * seg1.track.samplerate())

            if dur % 2 == 1:
                dur -= 1

            if dur / 2 > seg1.duration:
                dur = seg1.duration * 2

            if dur / 2 > seg2.duration:
                dur = seg2.duration * 2

            # we're going to compute the crossfade and then create a RawTrack
            # for the resulting frames

            seg1.duration += (dur / 2)
            out_frames = seg1.get_frames(channels=self.channels)[-dur:]
            seg1.duration -= dur
            
            seg2.start -= (dur / 2)
            seg2.duration += (dur / 2)
            seg2.score_location -= (dur / 2)
            in_frames = seg2.get_frames(channels=self.channels)[:dur]
            seg2.start += dur
            seg2.duration -= dur
            seg2.score_location += dur

            # compute the crossfade
            in_frames = in_frames[:min(map(len, [in_frames, out_frames]))]
            out_frames = out_frames[:min(map(len, [in_frames, out_frames]))]
            
            cf_frames = equal_power(out_frames, in_frames)
            
            #print "Computed cf_frames", cf_frames
            
            raw_track = RawTrack(cf_frames, name="crossfade",
                samplerate=seg1.track.samplerate())
            
            rs_score_location = (seg1.score_location + seg1.duration) /\
                float(seg1.track.samplerate())
                
            rs_duration = raw_track.duration()
            
            raw_seg = Segment(raw_track, rs_score_location, 0.0, rs_duration)
            
            self.add_track(raw_track)
            self.add_score_segment(raw_seg)
            
            return raw_seg
            
        else:
            print seg1.score_location + seg1.duration, seg2.score_location
            raise Exception("Segments must be adjacent to add a crossfade (%d, %d)" 
                % (seg1.score_location + seg1.duration, seg2.score_location))

    def cross_fade_linear(self, seg1, seg2, duration):
        if seg1.score_location + seg1.duration - seg2.score_location < 2:
            self.extended_fade_out(seg1, duration)
            self.fade_in(seg2, duration)
            # self.extended_fade_in(seg2, duration)
        else:
            print seg1.score_location + seg1.duration, seg2.score_location
            raise Exception("Segments must be adjacent to add a crossfade (%d, %d)"
                % (seg1.score_location + seg1.duration, seg2.score_location))

    def add_music_cue(self, track, score_cue, song_cue, duration=6.0,
                      padding_before=12.0, padding_after=12.0):
        self.tracks.add(track)
        
        pre_fade = 3
        post_fade = 3
        
        if padding_before + pre_fade > song_cue:
            padding_before = song_cue - pre_fade
            
        if padding_before + pre_fade > score_cue:
            padding_before = score_cue - pre_fade
                 
        # print "Composing %s at %.2f from %.2f to %.2f to %.2f to %.2f" % (
        #         track.filename, song_cue, score_cue-padding_before-pre_fade,
        #         score_cue, score_cue+duration,
        #         score_cue+duration+padding_after+post_fade)
        s = Segment(track, score_cue - padding_before - pre_fade,
                    song_cue - padding_before - pre_fade,
                    pre_fade + padding_before + duration + padding_after + post_fade)

        self.add_score_segment(s)
        
        d = []

        dyn_adj = 1
        
        track.set_frame(0)
        
         ## UNCOMMENT THIS STUFF! IT'S CORRECT!
        d.append(Fade(track, score_cue - padding_before - pre_fade, pre_fade,
                      0, .1*dyn_adj, fade_type="linear"))
        d.append(Fade(track, score_cue - padding_before, padding_before,
                      .1*dyn_adj, .4*dyn_adj, fade_type="exponential"))
        d.append(Volume(track, score_cue, duration, .4*dyn_adj))
        d.append(Fade(track, score_cue + duration, padding_after,
                      .4*dyn_adj, 0, fade_type="exponential"))
        # print "\n\n\n\n#####", score_cue+duration+padding_after, post_fade
        d.append(Fade(track, score_cue + duration + padding_after, post_fade,
                      .1*dyn_adj, 0, fade_type="linear"))
        self.add_dynamics(d)
    
    def _remove_end_silence(self, frames):
        subwindow_n_frames = int(1/16.0 * 44100)

        segments = segmentaxis.segment_axis(frames, subwindow_n_frames, axis=0,
                                     overlap=int(subwindow_n_frames / 2.0))

        # segments = segments.reshape((-1, subwindow_n_frames * 2))
        #volumes = N.mean(N.abs(segments), 1)
        volumes = N.apply_along_axis(RMS_energy, 1, segments)

        if DEBUG: print volumes
        min_subwindow_vol = min(N.sum(N.abs(segments), 1) /\
                            subwindow_n_frames)
        min_subwindow_vol = min(volumes)
        if DEBUG: print min_subwindow_vol
        # some threshold? what if there are no zeros?
    
        min_subwindow_vol_index = N.where(volumes <= 2.0 * 
                                          min_subwindow_vol)

        # first_min_subwindow = min_subwindow_vol_index[0][0]
        # closest_min_subwindow = find_nearest(min_subwindow_vol_index[0], 
        #                                      len(volumes)/2)
    
        # find longest span of "silence" and set to the beginning
        # adapted from 
        # http://stackoverflow.com/questions/3109052/
        # find-longest-span-of-consecutive-array-keys
        last_key = -1
        cur_list = []
        long_list = []
        for idx in min_subwindow_vol_index[0]:
            if idx != last_key + 1:
                cur_list = []
            cur_list.append(idx)
            if(len(cur_list) > len(long_list)):
                long_list = cur_list
            last_key = idx
    
        new_cut_point =  (long_list[0] + 1) * \
                         int(subwindow_n_frames / 2.0)

        if long_list[-1] + 16 > len(volumes):
            return frames[:new_cut_point]
        return frames
    
    def build_score(self, **kwargs):
        track_list = kwargs.pop('track', self.tracks)
        adjust_dynamics = kwargs.pop('adjust_dynamics', True)
        min_length = kwargs.pop('min_length', None)

        parts = {}
        starts = {}
        
        # for universal volume adjustment
        all_frames = N.array([])
        song_frames = N.array([])
        speech_frames = N.array([])
        
        longest_part = max([x.score_location + x.duration for x in self.score])
        
        for track_idx, track in enumerate(track_list):
            segments = sorted([v for v in self.score if v.track == track], 
                              key=lambda k: k.score_location + k.duration)
            if len(segments) > 0:
                start_loc = min([x.score_location for x in segments])
                end_loc = max([x.score_location + x.duration for x in segments])
                # end_loc = segments[-1].score_location + segments[-1].duration
                
                starts[track] = start_loc
                
                # print "start loc", start_loc, "end loc", end_loc
                # print "durs", [x.duration for x in segments]

                parts[track] = N.zeros((end_loc - start_loc, self.channels))
                
                for s in segments:
                    frames = s.get_frames(channels=self.channels).\
                        reshape(-1, self.channels)
                    
                    # for universal volume adjustment
                    if adjust_dynamics:
                        all_frames = N.append(all_frames,
                            self._remove_end_silence(frames.flatten()))
                        if isinstance(track, Song):
                            song_frames = N.append(song_frames, 
                                self._remove_end_silence(frames.flatten()))
                        elif isinstance(track, Speech):
                            speech_frames = N.append(speech_frames,
                                self._remove_end_silence(frames.flatten()))
                                
                    parts[track][s.score_location - start_loc:
                                 s.score_location - start_loc + s.duration,
                                 :] = frames

            dyns = sorted([d for d in self.dynamics if d.track == track],
                           key=lambda k: k.score_location)
            for d in dyns:
                vol_frames = d.to_array(self.channels)
                parts[track][d.score_location - start_loc :
                             d.score_location - start_loc + d.duration,
                             :] *= vol_frames

        if adjust_dynamics:
            total_energy = RMS_energy(all_frames)
            song_energy = RMS_energy(song_frames)
            speech_energy = RMS_energy(speech_frames)
                
        # dyn_adj = 0.10 / total_energy
        # dyn_adj = speech_energy / sqrt(song_energy) * 5
        if adjust_dynamics:
            if not N.isnan(speech_energy) and not N.isnan(song_energy):
                dyn_adj = sqrt(speech_energy / song_energy) * 1.15
            else:
                dyn_adj = 1
        else:
            dyn_adj = 1

        if longest_part < min_length:
            longest_part = min_length
        out = N.zeros((longest_part, self.channels))
        for track, part in parts.iteritems():
            out[starts[track]:starts[track] + len(part)] += part
        
        return out
    
    def output_score(self, **kwargs):
        # get optional args
        filename = kwargs.pop('filename', 'out')
        filetype = kwargs.pop('filetype', 'wav')
        adjust_dynamics = kwargs.pop('adjust_dynamics', True)
        samplerate = kwargs.pop('samplerate', 44100)
        channels = kwargs.pop('channels', 2)
        separate_tracks = kwargs.pop('separate_tracks', False)
        min_length = kwargs.pop('min_length', None)
        
        encoding = 'pcm16'
        if filetype == 'ogg':
            encoding = 'vorbis'
        
        if separate_tracks:
            for track in self.tracks:
                out = self.build_score(track=[track],
                                       adjust_dynamics=adjust_dynamics,
                                       min_length=min_length)
                out_file = Sndfile(filename +"-" + track.name + "." +
                                   filetype, 'w',
                                   Format(filetype, encoding=encoding),
                                   channels, samplerate)
                out_file.write_frames(out)
                out_file.close()

        # always build the complete score
        out = self.build_score(adjust_dynamics=adjust_dynamics,
                               min_length=min_length)

        out_file = Sndfile(filename + "." + filetype, 'w',
                           Format(filetype, encoding=encoding), 
                           channels, samplerate)
        out_file.write_frames(out)
        out_file.close()
        return out
