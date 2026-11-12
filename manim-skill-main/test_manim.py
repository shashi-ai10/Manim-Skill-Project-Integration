from manim import *

config.pixel_width = 854
config.pixel_height = 480
config.frame_rate = 15

class SquareToCircle(Scene):
    def construct(self):
        square = Square(color=BLUE)
        circle = Circle(color=GREEN)

        self.play(Create(square))
        self.play(Transform(square, circle), run_time=1)
        self.wait(5.15)