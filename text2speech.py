import os
import numpy as np
import sys
import time
import argparse
import torch
import matplotlib.pyplot as plt
from scipy.io.wavfile import write
from hparams import create_hparams
from model import Tacotron2
from text import text_to_sequence
sys.path.append('waveglow/')
from waveglow.mel2samp import MAX_WAV_VALUE
from waveglow.glow import WaveGlow
#from denoiser import Denoiser
from pydub import AudioSegment, effects

import json

class T2S:
    def __init__(self, model_choice):
        self.model_choice = model_choice
        self.hparams = create_hparams()
        self.hparams.sampling_rate = 22050
        with open('config.json', 'r') as f:
            self.config = json.load(f)
        self.max_duration_s = self.config.get('max_duration_s')
        self.hparams.max_decoder_steps = int(86.0 * self.max_duration_s)

        self.waveglow = torch.load('waveglow', map_location=torch.device('cpu'))['model']
        self.waveglow.eval()

        for m in self.waveglow.modules():
            if 'Conv' in str(type(m)):
                setattr(m, 'padding_mode', 'zeros')
                
        for k in self.waveglow.convinv:
            k.float()
        #self.denoiser = Denoiser(self.waveglow)
        self.update_model(model_choice, self.max_duration_s)

    
    def load_model(self):
        model = Tacotron2(self.hparams)
        if self.hparams.fp16_run:
            model.decoder.attention_layer.score_mask_value = finfo('float16').min

        if self.hparams.distributed_run:
            model = apply_gradient_allreduce(model)

        return model

    def tts(self, text, filename=None):
        if not filename:
            filename = str(time.time())
        sequence = np.array(text_to_sequence(text, [self.cleaner]))[None, :]
        sequence = torch.autograd.Variable(torch.from_numpy(sequence)).long()
        mel_outputs, mel, _, alignments = self.model.inference(sequence)
        mel_outputs = mel_outputs.to('cpu')
        mel = mel.to('cpu') 
        with torch.no_grad():
            audio = self.waveglow.infer(mel, sigma=0.666)
            audio = audio * MAX_WAV_VALUE
        # audio = self.denoiser(audio, strength=0.01)[:, 0]
        audio = audio.squeeze()
        audio = audio.cpu().numpy()
        audio = audio.astype('int16')
        audio_path =f"{filename}.wav"
        save_path = os.path.join('wavs',audio_path)
        write(save_path, self.hparams.sampling_rate, audio)
        # normalize volume
        pre_norm = AudioSegment.from_file(save_path, "wav")
        post_norm = effects.normalize(pre_norm)
        post_norm.export(save_path, format="wav")
        print("audio saved at: {}".format(save_path))
        return audio_path
        
        

    def update_model(self, model_choice, max_duration_s):
        # in case someone tries to bypass form validation and overload servers
        if max_duration_s > 12.0:
            self.hparams.max_decoder_steps=1024
        else:
            self.hparams.max_decoder_steps = int(86.0 * max_duration_s)
        self.cleaner = 'english_cleaners'
        self.model_choice = model_choice
        self.checkpoint_path = self.config.get('model').get(self.model_choice) 
        self.model = self.load_model()
        self.model.load_state_dict(torch.load(self.checkpoint_path, map_location=torch.device('cpu'))['state_dict'])
        _ = self.model.eval()
        return self
